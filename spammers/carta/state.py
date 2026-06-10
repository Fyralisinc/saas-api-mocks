"""Per-process state for the Carta mock.

Mirrors spammers/ramp/state.py — a single pool + the ``run_id`` of the active
director run, resolved at startup. The corpus seed layer writes through it; the
app reads the one issuer row that belongs to the run.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

import asyncpg

from spammers.common.db import create_pool


@dataclass
class CartaMockState:
    pool: asyncpg.Pool
    run_id: UUID


_STATE: Optional[CartaMockState] = None


async def startup(run_id: UUID | None = None) -> CartaMockState:
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
    _STATE = CartaMockState(pool=pool, run_id=rid)
    return _STATE


async def shutdown() -> None:
    global _STATE
    if _STATE is not None:
        await _STATE.pool.close()
        _STATE = None


def state() -> CartaMockState:
    if _STATE is None:
        raise RuntimeError("Carta mock state not initialised")
    return _STATE


async def issuer_for_run(pool: asyncpg.Pool, run_id: UUID) -> Optional[asyncpg.Record]:
    """Return this run's Carta issuer row, or None if not provisioned yet."""
    return await pool.fetchrow(
        "SELECT id, base_url, issuer_id, legal_name, doing_business_as_name, website, "
        "client_id, client_secret, access_token "
        "FROM app_carta.issuers WHERE run_id = $1",
        run_id,
    )
