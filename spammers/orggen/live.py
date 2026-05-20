"""Live event generator.

While the historical timeline (compile.py) populates events backwards from
virtual_now and marks them is_historical=TRUE (pull-API only), this module
generates NEW events that are dated forward of virtual_now and marked
is_historical=FALSE — the emission loop picks them up and delivers them as
signed webhooks.

Two entry points:
  - ``inject_slack_message(...)``  — one-off, fully parameterized
  - ``LiveEventGenerator(...)``    — long-running, samples at a target rate
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

import asyncpg
import structlog

from spammers.common.clock import get_clock
from spammers.orggen.profiles import resolve
from spammers.orggen.render import render
from spammers.orggen.seed import RunRandom


log = structlog.get_logger("spammers.orggen.live")


async def inject_slack_message(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    handle: Optional[str] = None,
    channel: Optional[str] = None,
    text: Optional[str] = None,
    at_virtual: Optional[datetime] = None,
) -> UUID:
    """Append one ``slack.message`` event to the timeline as not-historical.

    Defaults: random person, #general, a banter line, virtual_now + 1s.
    Returns the new event id.
    """
    if handle is None:
        row = await pool.fetchrow(
            "SELECT id, handle FROM org.people WHERE run_id = $1 ORDER BY random() LIMIT 1",
            run_id,
        )
    else:
        row = await pool.fetchrow(
            "SELECT id, handle FROM org.people WHERE run_id = $1 AND handle = $2",
            run_id, handle,
        )
    if row is None:
        raise LookupError("no people in this run; did you forget `prepare`?")
    actor_id = row["id"]

    clock = await get_clock(pool, run_id)
    when = at_virtual or (clock.virtual_now + timedelta(seconds=1))
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    if text is None:
        text = f"[live] hello from {row['handle']} @ {when.isoformat()}"
    if channel is None:
        channel = "#general"

    event_id = uuid4()
    await pool.execute(
        """
        INSERT INTO timeline.events
            (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical)
        VALUES ($1, $2, $3, 'slack.message', $4, $5::jsonb, '{}'::jsonb, FALSE)
        """,
        event_id, run_id, when, actor_id,
        json.dumps({"channel": channel, "text": text, "kind": "live"}),
    )
    return event_id


class LiveEventGenerator:
    """Long-running generator that produces live slack messages.

    Targets ``msgs_per_minute`` events per minute (drawn from a Poisson-ish
    distribution). Each event gets a virtual_ts slightly ahead of the current
    virtual_now so the emission loop picks it up on the next tick.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        run_id: UUID,
        *,
        msgs_per_minute: float = 6.0,
        seed_extra: int = 0,
    ) -> None:
        self._pool = pool
        self._run_id = run_id
        self._msgs_per_minute = msgs_per_minute
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._seed_extra = seed_extra

    async def _loop(self) -> None:
        # mean wait between events = 60 / rate
        # We pick from people/projects deterministically per run + a tick counter
        row = await self._pool.fetchrow(
            "SELECT size, runtime, seed FROM org.runs WHERE id = $1",
            self._run_id,
        )
        if row is None:
            return
        spec = resolve(row["size"], row["runtime"])
        rng = RunRandom(int(row["seed"]) + self._seed_extra, "live")

        people = [dict(r) for r in await self._pool.fetch(
            "SELECT id, handle, team_name FROM org.people p "
            "LEFT JOIN org.teams t ON t.id = p.team_id "
            "WHERE p.run_id = $1",
            self._run_id,
        )]
        if not people:
            return

        tick = 0
        while not self._stop.is_set():
            tick += 1
            interval = max(0.2, 60.0 / self._msgs_per_minute * rng.uniform(0.5, 1.5))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                pass

            person = rng.choice(people)
            text = render(
                "slack/work_update.j2",
                persona=type("P", (), {"voice_signature": {"formality": "casual"}})(),
                event_kind="ask",
                pr_link="",
                pr_title="",
                channel="",
                incident_summary="",
                question=rng.choice([
                    "anyone seen the build break on main?",
                    "is the cache ttl configurable from env?",
                    "what's the canonical way to log a tenant id?",
                    "do we have a runbook for the gateway timeout?",
                ]),
                default_text="",
            )
            try:
                await inject_slack_message(
                    self._pool,
                    self._run_id,
                    handle=person["handle"],
                    channel="#general",
                    text=text,
                )
            except Exception as exc:
                log.warning("live_inject_failed", error=str(exc))

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None
