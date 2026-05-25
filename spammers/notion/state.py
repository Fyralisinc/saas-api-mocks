"""Per-process state for the Notion mock.

Resolves the latest run and caches its integration (bot token + ids) at startup.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

import asyncpg

from spammers.common.db import create_pool


@dataclass
class NotionMockState:
    pool: asyncpg.Pool
    run_id: UUID
    integration_pk: UUID
    bot_token: str
    bot_user_id: str
    bot_name: str
    workspace_id: str
    workspace_name: str


_STATE: Optional[NotionMockState] = None


async def startup(run_id: UUID | None = None) -> NotionMockState:
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
    integ = await pool.fetchrow(
        "SELECT * FROM app_notion.integrations WHERE run_id = $1", rid,
    )
    if integ is None:
        raise RuntimeError(f"no notion integration for run {rid}; re-run prepare")
    _STATE = NotionMockState(
        pool=pool, run_id=rid, integration_pk=integ["id"], bot_token=integ["bot_token"],
        bot_user_id=integ["bot_user_id"], bot_name=integ["bot_name"],
        workspace_id=integ["workspace_id"], workspace_name=integ["workspace_name"],
    )
    return _STATE


async def shutdown() -> None:
    global _STATE
    if _STATE is not None:
        await _STATE.pool.close()
        _STATE = None


def state() -> NotionMockState:
    if _STATE is None:
        raise RuntimeError("notion mock state not initialised — call startup() first")
    return _STATE
