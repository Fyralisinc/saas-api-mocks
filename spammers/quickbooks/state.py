"""Per-process state for the QuickBooks Online mock.

Mirrors the same shape as spammers/drive/state.py — a single pool + the
``run_id`` of the currently-active director run. The state is resolved at
startup; the corpus replay layer writes through it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

import asyncpg

from spammers.common.db import create_pool


@dataclass
class QuickBooksMockState:
    pool: asyncpg.Pool
    run_id: UUID


_STATE: Optional[QuickBooksMockState] = None


async def startup(run_id: UUID | None = None) -> QuickBooksMockState:
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
    _STATE = QuickBooksMockState(pool=pool, run_id=rid)
    return _STATE


async def shutdown() -> None:
    global _STATE
    if _STATE is not None:
        await _STATE.pool.close()
        _STATE = None


def state() -> QuickBooksMockState:
    if _STATE is None:
        raise RuntimeError("QuickBooks mock state not initialised")
    return _STATE


async def realm_id_for_run(pool: asyncpg.Pool, run_id: UUID) -> Optional[str]:
    """Return the realm_id of this run's company, or None if not provisioned yet."""
    return await pool.fetchval(
        "SELECT realm_id FROM app_quickbooks.companies WHERE run_id = $1",
        run_id,
    )


async def company_pk_for_realm(pool: asyncpg.Pool, run_id: UUID, realm_id: str) -> Optional[str]:
    return await pool.fetchval(
        "SELECT id FROM app_quickbooks.companies "
        "WHERE run_id = $1 AND realm_id = $2",
        run_id, realm_id,
    )
