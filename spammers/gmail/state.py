"""Per-process state for the Gmail mock.

Resolves the latest run and caches its customer (domain + Pub/Sub OIDC keys).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

import asyncpg

from spammers.common.db import create_pool


@dataclass
class GmailMockState:
    pool: asyncpg.Pool
    run_id: UUID
    customer_pk: UUID
    customer_id: str
    domain: str
    organization_name: str
    service_account_email: str
    oidc_private_key: str
    oidc_public_key: str
    pubsub_audience: str


_STATE: Optional[GmailMockState] = None


async def startup(run_id: UUID | None = None) -> GmailMockState:
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
    cust = await pool.fetchrow("SELECT * FROM app_gmail.customers WHERE run_id = $1", rid)
    if cust is None:
        raise RuntimeError(f"no gmail customer for run {rid}; re-run prepare")
    _STATE = GmailMockState(
        pool=pool, run_id=rid, customer_pk=cust["id"], customer_id=cust["customer_id"],
        domain=cust["domain"], organization_name=cust["organization_name"],
        service_account_email=cust["service_account_email"],
        oidc_private_key=cust["pubsub_oidc_private_key"],
        oidc_public_key=cust["pubsub_oidc_public_key"],
        pubsub_audience=cust["pubsub_audience"],
    )
    return _STATE


async def shutdown() -> None:
    global _STATE
    if _STATE is not None:
        await _STATE.pool.close()
        _STATE = None


def state() -> GmailMockState:
    if _STATE is None:
        raise RuntimeError("gmail mock state not initialised — call startup() first")
    return _STATE
