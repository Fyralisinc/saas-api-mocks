"""Per-process state for the Fireflies mock.

Mirrors spammers/ramp/state.py — a single pool + the ``run_id`` of the active
director run, resolved at startup. The corpus seed writes through it; the app
reads the one workspace row that belongs to the run.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

import asyncpg

from spammers.common.db import create_pool


@dataclass
class FirefliesMockState:
    pool: asyncpg.Pool
    run_id: UUID


_STATE: Optional[FirefliesMockState] = None


async def startup(run_id: UUID | None = None) -> FirefliesMockState:
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
            row = await pool.fetchrow(
                "SELECT id FROM org.runs ORDER BY created_at DESC LIMIT 1")
            if row is None:
                raise RuntimeError(
                    "no run found in org.runs; create one via Director first")
            rid = row["id"]
    _STATE = FirefliesMockState(pool=pool, run_id=rid)
    return _STATE


async def shutdown() -> None:
    global _STATE
    if _STATE is not None:
        await _STATE.pool.close()
        _STATE = None


def state() -> FirefliesMockState:
    if _STATE is None:
        raise RuntimeError("Fireflies mock state not initialised")
    return _STATE


async def workspace_for_run(pool: asyncpg.Pool, run_id: UUID) -> Optional[asyncpg.Record]:
    """Return this run's Fireflies workspace row, or None if not provisioned yet."""
    return await pool.fetchrow(
        "SELECT id, base_url, team_name, api_token, webhook_secret, "
        "owner_user_id, owner_email, owner_name "
        "FROM app_fireflies.workspaces WHERE run_id = $1",
        run_id,
    )
