"""Per-process state for the Jira mock (mirrors calendar/state.py)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

import asyncpg

from spammers.common.db import create_pool


@dataclass
class JiraMockState:
    pool: asyncpg.Pool
    run_id: UUID


_STATE: Optional[JiraMockState] = None


async def startup(run_id: UUID | None = None) -> JiraMockState:
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
            row = await pool.fetchrow("SELECT id FROM org.runs ORDER BY created_at DESC LIMIT 1")
            if row is None:
                raise RuntimeError("no run found in org.runs; create one via Director first")
            rid = row["id"]
    _STATE = JiraMockState(pool=pool, run_id=rid)
    return _STATE


async def shutdown() -> None:
    global _STATE
    if _STATE is not None:
        await _STATE.pool.close()
        _STATE = None


def state() -> JiraMockState:
    if _STATE is None:
        raise RuntimeError("jira mock state not initialised — call startup() first")
    return _STATE
