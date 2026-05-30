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
           "drive": 7007, "jira": 7008}
_PY = sys.executable
_CLI = [_PY, "-m", "spammers.director.cli"]
_SERVER_MODULE = {"slack": "spammers.slack", "discord": "spammers.discord",
                  "github": "spammers.github", "gmail": "spammers.gmail",
                  "calendar": "spammers.calendar", "notion": "spammers.notion",
                  "drive": "spammers.drive", "jira": "spammers.jira"}


@dataclass
class State:
    running: bool = False
    busy: bool = False              # a start/stop is in flight
    phase: str = "idle"             # idle | resetting | seeding | launching | running | stopping
    company_key: Optional[str] = None
    error: Optional[str] = None
    servers: dict = field(default_factory=dict)   # provider -> "up"|"down"

    def public(self) -> dict:
        comp = companies.get(self.company_key).as_dict() if self.company_key else None
        return {
            "running": self.running, "busy": self.busy, "phase": self.phase,
            "company": comp, "error": self.error,
            "servers": self.servers or {p: "down" for p in SERVERS},
            "ports": SERVERS,
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

    async def start(self, company_key: str) -> dict:
        comp = companies.get(company_key)
        if comp is None:
            raise ValueError(f"unknown company: {company_key}")
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
                # Backfill corpus up to 2025-11-28 — matches dev.sh's default.
                # That leaves ~11 months of corpus for forward-replay to land
                # after the mocks come up, so counts visibly tick up in the UI
                # instead of going straight to "all 34k events landed".
                await self._run_cli(
                    "prepare", "--tenant-id", str(uuid.uuid4()),
                    "--as-of", "2025-11-28",
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

                # Start emit: clock ticker + corpus forward-replay + webhook
                # emission. Speed 1800× → 1 virtual day per ~48 real-sec.
                # The corpus is sparse (~10k slack msgs across 47mo = ~0.3
                # events/virtual-hour on average), so anything slower means
                # multi-second stretches of static UI even during workday
                # hours. At 1800× peak hours land an event per ~1s of real
                # time and a 24h day finishes in under a minute. The
                # remaining ~11mo of corpus exhausts in ~5 real hours.
                self._spawn_emit(speed=1800.0)

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
