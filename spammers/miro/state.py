"""Per-process state for the Miro mock.

Mirrors spammers/figma/state.py — a single pool + the ``run_id`` of the active
director run, resolved at startup. The corpus seed layer writes through it; the
app reads the one org row that belongs to the run.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

import asyncpg

from spammers.common.db import create_pool


@dataclass
class MiroMockState:
    pool: asyncpg.Pool
    run_id: UUID


_STATE: Optional[MiroMockState] = None


async def startup(run_id: UUID | None = None) -> MiroMockState:
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
    _STATE = MiroMockState(pool=pool, run_id=rid)
    return _STATE


async def shutdown() -> None:
    global _STATE
    if _STATE is not None:
        await _STATE.pool.close()
        _STATE = None


def state() -> MiroMockState:
    if _STATE is None:
        raise RuntimeError("Miro mock state not initialised")
    return _STATE


async def org_for_run(pool: asyncpg.Pool, run_id: UUID) -> Optional[asyncpg.Record]:
    """Return this run's Miro org row, or None if not provisioned yet."""
    return await pool.fetchrow(
        "SELECT id, base_url, org_id, org_name, team_id, team_name, access_token "
        "FROM app_miro.orgs WHERE run_id = $1",
        run_id,
    )
