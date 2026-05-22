"""Per-process state for the Discord mock (mirrors spammers/github/state.py).

In addition to the pool/run_id/rate_limiter the other mocks hold, the Discord
mock owns a ``SessionHub`` (live Gateway WebSocket connections) and a
``GatewayDispatcher`` (the background task that turns ``discord.message``
timeline events into ``MESSAGE_CREATE`` dispatches). Both are populated by the
FastAPI lifespan; tests set ``_STATE`` by hand and start the dispatcher
explicitly (ASGITransport skips lifespan).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional
from uuid import UUID

import asyncpg

from spammers.common.db import create_pool
from spammers.common.rate_limit import RateLimiter

if TYPE_CHECKING:  # avoid import cycle; gateway imports state
    from spammers.discord.gateway.dispatcher import GatewayDispatcher
    from spammers.discord.gateway.hub import SessionHub


@dataclass
class DiscordMockState:
    pool: asyncpg.Pool
    run_id: UUID
    rate_limiter: RateLimiter
    hub: "SessionHub"
    dispatcher: "Optional[GatewayDispatcher]" = field(default=None)


_STATE: Optional[DiscordMockState] = None


async def startup(run_id: UUID | None = None) -> DiscordMockState:
    global _STATE
    if _STATE is not None:
        return _STATE
    from spammers.discord.gateway.hub import SessionHub

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
    _STATE = DiscordMockState(
        pool=pool, run_id=rid, rate_limiter=RateLimiter(), hub=SessionHub(),
    )
    return _STATE


async def shutdown() -> None:
    global _STATE
    if _STATE is not None:
        if _STATE.dispatcher is not None:
            await _STATE.dispatcher.stop()
        await _STATE.hub.close_all()
        await _STATE.pool.close()
        _STATE = None


def state() -> DiscordMockState:
    if _STATE is None:
        raise RuntimeError("discord mock state not initialised — call startup() first")
    return _STATE
