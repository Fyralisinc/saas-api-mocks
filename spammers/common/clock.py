"""Virtual clock.

Every mock reads virtual time from the Director's authoritative clock,
stored in ``org.runs.virtual_now``. The mocks render API responses
and stamp webhook deliveries using this clock — never wall time.

Modes (stored in ``org.runs.mode``):
  - 'frozen'  → virtual_now does not advance
  - 'live'    → advances at wall × speed_multiplier
  - 'step'    → advances only when Director POSTs /control/jump

Speed multiplier is `org.runs.speed_multiplier` (default 1.0).

For the slack-mock and friends, use ``VirtualClock(pool, run_id).now()``
or the cached-per-request shortcut ``await get_virtual_now(pool, run_id)``.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

import asyncpg


@dataclass(frozen=True)
class ClockState:
    virtual_now: datetime
    mode: str
    speed_multiplier: float


async def get_clock(pool: asyncpg.Pool, run_id: UUID) -> ClockState:
    row = await pool.fetchrow(
        "SELECT virtual_now, mode, speed_multiplier FROM org.runs WHERE id = $1",
        run_id,
    )
    if row is None:
        raise LookupError(f"run not found: {run_id}")
    return ClockState(
        virtual_now=row["virtual_now"],
        mode=row["mode"],
        speed_multiplier=float(row["speed_multiplier"]),
    )


async def advance(pool: asyncpg.Pool, run_id: UUID, by: timedelta) -> datetime:
    row = await pool.fetchrow(
        """
        UPDATE org.runs
           SET virtual_now = virtual_now + $2
         WHERE id = $1
         RETURNING virtual_now
        """,
        run_id, by,
    )
    if row is None:
        raise LookupError(f"run not found: {run_id}")
    return row["virtual_now"]


async def jump_to(pool: asyncpg.Pool, run_id: UUID, when: datetime) -> datetime:
    when_utc = when.astimezone(timezone.utc) if when.tzinfo else when.replace(tzinfo=timezone.utc)
    row = await pool.fetchrow(
        "UPDATE org.runs SET virtual_now = $2 WHERE id = $1 RETURNING virtual_now",
        run_id, when_utc,
    )
    if row is None:
        raise LookupError(f"run not found: {run_id}")
    return row["virtual_now"]


async def set_mode(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    mode: Optional[str] = None,
    speed_multiplier: Optional[float] = None,
) -> ClockState:
    sets = []
    args: list = []
    if mode is not None:
        if mode not in ("frozen", "live", "step"):
            raise ValueError(f"bad mode: {mode}")
        args.append(mode)
        sets.append(f"mode = ${len(args) + 1}")
    if speed_multiplier is not None:
        args.append(float(speed_multiplier))
        sets.append(f"speed_multiplier = ${len(args) + 1}")
    if not sets:
        return await get_clock(pool, run_id)
    sql = f"UPDATE org.runs SET {', '.join(sets)} WHERE id = $1 RETURNING virtual_now, mode, speed_multiplier"
    row = await pool.fetchrow(sql, run_id, *args)
    if row is None:
        raise LookupError(f"run not found: {run_id}")
    return ClockState(
        virtual_now=row["virtual_now"],
        mode=row["mode"],
        speed_multiplier=float(row["speed_multiplier"]),
    )


class LiveClockTicker:
    """Drives ``org.runs.virtual_now`` forward when ``mode = 'live'``.

    The Director runs one ticker per run. Tick cadence: every 1s of wall time
    advances virtual_now by 1s × speed_multiplier. A no-op when mode != 'live'.
    """

    def __init__(self, pool: asyncpg.Pool, run_id: UUID, tick_s: float = 1.0) -> None:
        self._pool = pool
        self._run_id = run_id
        self._tick_s = tick_s
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                state = await get_clock(self._pool, self._run_id)
                if state.mode == "live":
                    delta = timedelta(seconds=self._tick_s * state.speed_multiplier)
                    await advance(self._pool, self._run_id, delta)
            except Exception:
                # The Director's main loop owns observability; swallow here.
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._tick_s)
            except asyncio.TimeoutError:
                continue

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None
