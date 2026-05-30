"""Per-run replay cursor management.

The cursor lives in ``org.runs.replay_cursor``. ``backfill`` advances it to a
target timestamp; the daemon (``grow``) advances it by ``rate * elapsed`` on
each tick. ``jump`` snaps it forward to a target.

The cursor is **idempotent**: re-running ``advance(...)`` to the same value is
a no-op and re-running replay against the same cursor produces zero new rows
(handlers check existence by corpus_id before inserting).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import asyncpg


async def get(pool: asyncpg.Pool, run_id: UUID) -> datetime | None:
    return await pool.fetchval(
        "SELECT replay_cursor FROM org.runs WHERE id = $1", run_id,
    )


async def set_(pool: asyncpg.Pool, run_id: UUID, ts: datetime) -> None:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    await pool.execute(
        "UPDATE org.runs SET replay_cursor = $2 WHERE id = $1", run_id, ts,
    )


async def advance(pool: asyncpg.Pool, run_id: UUID, ts: datetime) -> datetime:
    """Move the cursor forward to ``ts``; never backward. Returns the new value."""
    current = await get(pool, run_id)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if current is not None and ts < current:
        return current
    await set_(pool, run_id, ts)
    return ts
