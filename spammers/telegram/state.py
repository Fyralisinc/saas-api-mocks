"""Per-process state for the Telegram mock (mirrors spammers/discord/state.py).

Like Discord (the gateway analog the flow doc names), the Telegram mock owns a
``SessionHub`` (live updates-connection WebSockets) and an ``UpdatesDispatcher``
(the background task that turns ``telegram.message`` timeline events into
``updateNewMessage`` / ``updateEditMessage`` pushes). Both are populated by the
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

if TYPE_CHECKING:  # avoid import cycle; gateway imports state
    from spammers.telegram.gateway import SessionHub, UpdatesDispatcher


@dataclass
class TelegramMockState:
    pool: asyncpg.Pool
    run_id: UUID
    hub: "SessionHub"
    dispatcher: "Optional[UpdatesDispatcher]" = field(default=None)
    # Mock-only knob: arm a one-shot FLOOD_WAIT on the next read (the protocol's
    # own backpressure — RPC error 420 + server seconds; see /_control).
    flood_wait_seconds: Optional[int] = field(default=None)


_STATE: Optional[TelegramMockState] = None


async def startup(run_id: UUID | None = None) -> TelegramMockState:
    global _STATE
    if _STATE is not None:
        return _STATE
    from spammers.telegram.gateway import SessionHub

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
    _STATE = TelegramMockState(pool=pool, run_id=rid, hub=SessionHub())
    return _STATE


async def shutdown() -> None:
    global _STATE
    if _STATE is not None:
        if _STATE.dispatcher is not None:
            await _STATE.dispatcher.stop()
        await _STATE.hub.close_all()
        await _STATE.pool.close()
        _STATE = None


def state() -> TelegramMockState:
    if _STATE is None:
        raise RuntimeError("telegram mock state not initialised — call startup() first")
    return _STATE


async def install_for_run(pool: asyncpg.Pool, run_id: UUID) -> Optional[asyncpg.Record]:
    """Return this run's Telegram install row, or None if not provisioned yet."""
    return await pool.fetchrow(
        "SELECT id, account_label, session_string, api_id, api_hash, "
        "self_user_id, self_username, self_phone "
        "FROM app_telegram.installations WHERE run_id = $1",
        run_id,
    )
