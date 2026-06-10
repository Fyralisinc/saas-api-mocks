"""Per-process state for the AWS mock.

Mirrors spammers/grafana/state.py — a single pool + the ``run_id`` of the active
director run, resolved at startup. The corpus seed layer writes through it; the
app reads the one install row that belongs to the run.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

import asyncpg

from spammers.common.db import create_pool


@dataclass
class AwsMockState:
    pool: asyncpg.Pool
    run_id: UUID


_STATE: Optional[AwsMockState] = None


async def startup(run_id: UUID | None = None) -> AwsMockState:
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
    _STATE = AwsMockState(pool=pool, run_id=rid)
    return _STATE


async def shutdown() -> None:
    global _STATE
    if _STATE is not None:
        await _STATE.pool.close()
        _STATE = None


def state() -> AwsMockState:
    if _STATE is None:
        raise RuntimeError("AWS mock state not initialised")
    return _STATE


async def install_for_run(pool: asyncpg.Pool, run_id: UUID) -> Optional[asyncpg.Record]:
    """Return this run's AWS install row, or None if not provisioned yet."""
    return await pool.fetchrow(
        "SELECT id, account_id, region, endpoint_host, access_key_id, "
        "secret_access_key, role_arn, external_id, iam_user_arn, user_id "
        "FROM app_aws.installations WHERE run_id = $1",
        run_id,
    )


async def secret_for_access_key(pool: asyncpg.Pool, run_id: UUID,
                                access_key_id: str) -> Optional[str]:
    """Resolve the static secret-access-key for an access_key_id (SigV4 verify).

    Single-tenant per run: there is exactly one install, so this matches the
    install's own seeded key. An unknown key returns None (→ InvalidClientTokenId).
    """
    return await pool.fetchval(
        "SELECT secret_access_key FROM app_aws.installations "
        "WHERE run_id = $1 AND access_key_id = $2",
        run_id, access_key_id,
    )
