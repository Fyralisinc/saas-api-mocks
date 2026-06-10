"""Per-process state for the Gusto mock.

Mirrors spammers/ramp/state.py — a single pool + the ``run_id`` of the active
director run, resolved at startup. The corpus seed layer writes through it; the
app reads the one company row that belongs to the run.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

import asyncpg

from spammers.common.db import create_pool


@dataclass
class GustoMockState:
    pool: asyncpg.Pool
    run_id: UUID


_STATE: Optional[GustoMockState] = None


async def startup(run_id: UUID | None = None) -> GustoMockState:
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
    _STATE = GustoMockState(pool=pool, run_id=rid)
    return _STATE


async def shutdown() -> None:
    global _STATE
    if _STATE is not None:
        await _STATE.pool.close()
        _STATE = None


def state() -> GustoMockState:
    if _STATE is None:
        raise RuntimeError("Gusto mock state not initialised")
    return _STATE


async def company_for_run(pool: asyncpg.Pool, run_id: UUID) -> Optional[asyncpg.Record]:
    """Return this run's Gusto company row, or None if not provisioned yet."""
    return await pool.fetchrow(
        "SELECT id, base_url, company_uuid, name, trade_name, ein, entity_type, "
        "company_status, tier, join_date, pay_schedule_uuid, client_id, "
        "client_secret, access_token, refresh_token, webhook_secret "
        "FROM app_gusto.companies WHERE run_id = $1",
        run_id,
    )


async def virtual_now(pool: asyncpg.Pool, run_id: UUID):
    """The run's frozen ``virtual_now`` — Gusto's 'present' for default windows."""
    return await pool.fetchval(
        "SELECT virtual_now FROM org.runs WHERE id = $1", run_id)
