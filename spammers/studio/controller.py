"""Process supervisor for the Studio.

Owns the lifecycle the UI's START/STOP buttons drive:

  START(company): free ports -> reset DB -> prepare (backfill corpus to as-of)
                  -> spawn the eight mock servers -> wait healthy
                  -> start emit (LiveClockTicker + CorpusReplayLoop + EmissionLoop)
                  so the clock advances and forward-replay events keep landing.
  STOP:           kill emit -> kill the eight servers -> reset DB.

The mocks resolve "the current run" once at startup, so a new company is
only served after the servers are (re)spawned — which is exactly what
START does. All process handling is best-effort and isolated; nothing
here is imported by the mocks themselves.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
import subprocess
import sys
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Optional

from spammers.studio import companies

# provider -> port
SERVERS = {"slack": 7001, "discord": 7002, "github": 7003,
           "gmail": 7004, "calendar": 7005, "notion": 7006,
           "drive": 7007, "jira": 7008, "quickbooks": 7009, "grafana": 7010,
           "mercury": 7011, "ashby": 7012, "brex": 7013, "deel": 7014,
           "hibob": 7015, "figma": 7016, "miro": 7017, "ramp": 7018,
           "gusto": 7019, "carta": 7020, "linkedin": 7021, "fireflies": 7022,
           "aws": 7023, "telegram": 7024}
_PY = sys.executable
_CLI = [_PY, "-m", "spammers.director.cli"]
_SERVER_MODULE = {"slack": "spammers.slack", "discord": "spammers.discord",
                  "github": "spammers.github", "gmail": "spammers.gmail",
                  "calendar": "spammers.calendar", "notion": "spammers.notion",
                  "drive": "spammers.drive", "jira": "spammers.jira",
                  "quickbooks": "spammers.quickbooks", "grafana": "spammers.grafana",
                  "mercury": "spammers.mercury", "ashby": "spammers.ashby",
                  "brex": "spammers.brex", "deel": "spammers.deel",
                  "hibob": "spammers.hibob", "figma": "spammers.figma",
                  "miro": "spammers.miro", "ramp": "spammers.ramp",
                  "gusto": "spammers.gusto", "carta": "spammers.carta",
                  "linkedin": "spammers.linkedin", "fireflies": "spammers.fireflies",
                  "aws": "spammers.aws", "telegram": "spammers.telegram"}


@dataclass
class State:
    running: bool = False
    busy: bool = False              # a start/stop is in flight
    phase: str = "idle"             # idle | resetting | seeding | launching | running | stopping
    company_key: Optional[str] = None
    error: Optional[str] = None
    servers: dict = field(default_factory=dict)   # provider -> "up"|"down"
    speed: float = 1800.0           # virtual-time multiplier the emit loop runs at
    paused: bool = False            # emit subprocess is currently SIGSTOPped

    def public(self) -> dict:
        comp = companies.get(self.company_key).as_dict() if self.company_key else None
        return {
            "running": self.running, "busy": self.busy, "phase": self.phase,
            "company": comp, "error": self.error,
            "servers": self.servers or {p: "down" for p in SERVERS},
            "ports": SERVERS,
            "speed": self.speed, "paused": self.paused,
        }


class Controller:
    def __init__(self) -> None:
        self.state = State()
        self._procs: dict[str, subprocess.Popen] = {}
        self._emit_proc: Optional[subprocess.Popen] = None
        self._lock = asyncio.Lock()

    # ---- low-level helpers -------------------------------------------------

    @staticmethod
    def _port_open(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            return s.connect_ex(("127.0.0.1", port)) == 0

    @staticmethod
    def _free_port(port: int) -> None:
        """Best-effort kill of whatever listens on a port (stale servers)."""
        for cmd in (["lsof", "-ti", f"tcp:{port}"], ["fuser", f"{port}/tcp"]):
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                pids = out.stdout.replace("\n", " ").split()
                for pid in pids:
                    with contextlib.suppress(Exception):
                        os.kill(int(pid), 9)
                if pids:
                    return
            except (FileNotFoundError, subprocess.SubprocessError):
                continue

    async def _run_cli(self, *args: str, timeout: float = 120.0) -> None:
        proc = await asyncio.create_subprocess_exec(
            *_CLI, *args, env=os.environ.copy(),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"director {args[0]} timed out")
        if proc.returncode != 0:
            tail = (out or b"").decode(errors="replace")[-800:]
            raise RuntimeError(f"director {args[0]} failed (exit {proc.returncode}):\n{tail}")

    async def _wait_healthy(self, port: int, timeout: float = 25.0) -> bool:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        url = f"http://127.0.0.1:{port}/_health"
        while loop.time() < deadline:
            try:
                ok = await asyncio.to_thread(self._probe, url)
                if ok:
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.4)
        return False

    @staticmethod
    def _probe(url: str) -> bool:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200

    def _spawn_servers(self) -> None:
        env = os.environ.copy()
        for provider, port in SERVERS.items():
            self._procs[provider] = subprocess.Popen(
                [_PY, "-m", _SERVER_MODULE[provider], "run", "--port", str(port)],
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )

    def _kill_servers(self) -> None:
        for provider, proc in list(self._procs.items()):
            with contextlib.suppress(Exception):
                proc.terminate()
        for provider, proc in list(self._procs.items()):
            with contextlib.suppress(Exception):
                proc.wait(timeout=5)
            with contextlib.suppress(Exception):
                proc.kill()
        self._procs.clear()
        for port in SERVERS.values():
            self._free_port(port)

    def _spawn_emit(self, speed: float) -> None:
        """Start `spammer emit` in the background. Drives the live clock,
        the corpus forward-replay, and the webhook emission loop."""
        self._emit_proc = subprocess.Popen(
            [*_CLI, "emit", "--speed", str(speed)],
            env=os.environ.copy(),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def _kill_emit(self) -> None:
        if self._emit_proc is None:
            return
        with contextlib.suppress(Exception):
            self._emit_proc.terminate()
        with contextlib.suppress(Exception):
            self._emit_proc.wait(timeout=5)
        with contextlib.suppress(Exception):
            self._emit_proc.kill()
        self._emit_proc = None

    # ---- public API --------------------------------------------------------

    async def start(self, company_key: str, speed: float = 1800.0) -> dict:
        comp = companies.get(company_key)
        if comp is None:
            raise ValueError(f"unknown company: {company_key}")
        # Clamp to sensible bounds: 1× (real-time, virtual-clock matches wall)
        # up to 10000× (corpus exhausts in ~1 hour). Above 10000× the loop
        # spends more time querying than landing events.
        speed = max(1.0, min(10000.0, float(speed)))
        async with self._lock:
            if self.state.busy:
                raise RuntimeError("a start/stop is already in progress")
            self.state.busy = True
            self.state.error = None
            self.state.company_key = company_key
            try:
                # clean slate
                self.state.phase = "stopping"
                self._kill_emit()
                self._kill_servers()

                self.state.phase = "resetting"
                await self._run_cli("reset", "--confirm", "yes")

                self.state.phase = "seeding"
                # Backfill the FIRST 18 months of the corpus (2024-02 → 2025-08)
                # so the company starts with the cofounder-only stretch, seed
                # round close, team formation, and strategic-round phase
                # already on-screen. The remaining ~10 months land via
                # forward-replay as the virtual clock advances, so the model
                # layer sees the testnet, Glock release, Mosaic, and Bitcoin
                # Dollar happen live.
                await self._run_cli(
                    "prepare", "--tenant-id", str(uuid.uuid4()),
                    "--as-of", "2025-08-01",
                    timeout=600.0,
                )

                self.state.phase = "launching"
                self._spawn_servers()
                healthy = {}
                for provider, port in SERVERS.items():
                    healthy[provider] = "up" if await self._wait_healthy(port) else "down"
                self.state.servers = healthy
                if any(v == "down" for v in healthy.values()):
                    raise RuntimeError(f"some servers failed to start: {healthy}")

                # Start emit at the user-chosen speed multiplier.
                #   1×    real-time (1 virtual sec per 1 real sec)
                #   60×   1 virtual minute per real sec
                #   600×  1 virtual day per ~2.4 real min
                #   1800× 1 virtual day per ~48 real sec (Studio default)
                #   3600× 1 virtual day per ~24 real sec
                #   10000×fast burn (~11mo corpus exhausts in ~80 real min)
                self._spawn_emit(speed=speed)
                self.state.speed = speed
                self.state.paused = False

                self.state.running = True
                self.state.phase = "running"
                return self.state.public()
            except Exception as e:
                self.state.error = str(e)
                self.state.running = False
                self.state.phase = "idle"
                self._kill_emit()
                self._kill_servers()
                raise
            finally:
                self.state.busy = False

    async def stop(self) -> dict:
        async with self._lock:
            if self.state.busy:
                raise RuntimeError("a start/stop is already in progress")
            self.state.busy = True
            self.state.phase = "stopping"
            try:
                # If we're paused, resume first so the emit subprocess can
                # exit cleanly on terminate — SIGSTOPped processes can't
                # handle SIGTERM.
                if self.state.paused:
                    with contextlib.suppress(Exception):
                        self._signal_emit(signal.SIGCONT)
                    self.state.paused = False
                self._kill_emit()
                self._kill_servers()
                with contextlib.suppress(Exception):
                    await self._run_cli("reset", "--confirm", "yes")
                self.state.running = False
                self.state.company_key = None
                self.state.servers = {p: "down" for p in SERVERS}
                self.state.phase = "idle"
                self.state.error = None
                return self.state.public()
            finally:
                self.state.busy = False

    def _signal_emit(self, sig: int) -> None:
        """Send a signal to the emit subprocess (SIGSTOP / SIGCONT)."""
        if self._emit_proc is None or self._emit_proc.poll() is not None:
            return
        os.kill(self._emit_proc.pid, sig)

    async def pause(self) -> dict:
        """Halt virtual time + data flow. SIGSTOPs the emit subprocess so
        LiveClockTicker, CorpusReplayLoop, and EmissionLoop all freeze in
        place. The provider mocks stay up — anything ingested so far is
        still queryable + the mocks still answer their APIs. Inject is
        still available."""
        async with self._lock:
            if not self.state.running:
                raise RuntimeError("not running")
            if self.state.paused:
                return self.state.public()
            self._signal_emit(signal.SIGSTOP)
            self.state.paused = True
            self.state.phase = "paused"
            return self.state.public()

    async def resume(self) -> dict:
        """Unfreeze emit — virtual time resumes, corpus replay picks up
        where it left off, webhooks resume firing."""
        async with self._lock:
            if not self.state.running:
                raise RuntimeError("not running")
            if not self.state.paused:
                return self.state.public()
            self._signal_emit(signal.SIGCONT)
            self.state.paused = False
            self.state.phase = "running"
            return self.state.public()

    def refresh_server_health(self) -> None:
        if self.state.running:
            self.state.servers = {
                p: ("up" if self._port_open(port) else "down")
                for p, port in SERVERS.items()
            }

    def shutdown(self) -> None:
        """Called on Studio process exit — don't orphan mock servers or emit."""
        self._kill_emit()
        self._kill_servers()
