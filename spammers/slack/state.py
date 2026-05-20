"""Per-process state for the Slack mock.

Holds the asyncpg pool, the active run_id, and the rate-limiter instance.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

import asyncpg

from spammers.common.db import create_pool
from spammers.common.rate_limit import RateLimiter


@dataclass
class SlackMockState:
    pool: asyncpg.Pool
    run_id: UUID
    rate_limiter: RateLimiter


_STATE: Optional[SlackMockState] = None


async def startup(run_id: UUID | None = None) -> SlackMockState:
    global _STATE
    if _STATE is not None:
        return _STATE
    pool = await create_pool()
    rid = run_id
    if rid is None:
        rid_env = os.environ.get("SPAMMER_RUN_ID")
        if rid_env:
            rid = UUID(rid_env)
        else:
            # take the most recent run
            row = await pool.fetchrow(
                "SELECT id FROM org.runs ORDER BY created_at DESC LIMIT 1"
            )
            if row is None:
                raise RuntimeError("no run found in org.runs; create one via Director first")
            rid = row["id"]
    _STATE = SlackMockState(pool=pool, run_id=rid, rate_limiter=RateLimiter())
    return _STATE


async def shutdown() -> None:
    global _STATE
    if _STATE is not None:
        await _STATE.pool.close()
        _STATE = None


def state() -> SlackMockState:
    if _STATE is None:
        raise RuntimeError("slack mock state not initialised — call startup() first")
    return _STATE
