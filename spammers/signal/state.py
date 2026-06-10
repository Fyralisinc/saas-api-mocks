"""Per-process state for the Signal mock (mirrors spammers/telegram/state.py).

Like Telegram/Discord (the gateway analog the flow doc names — Signal is "cloned
from Telegram"), the Signal mock owns a ``SessionHub`` (live receive-connection
WebSockets) and a ``ReceiveDispatcher`` (the background task that turns
``signal.message`` timeline events into ``receive`` notification pushes). Both are
populated by the FastAPI lifespan; tests set ``_STATE`` by hand and start the
dispatcher explicitly (ASGITransport skips lifespan).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional
from uuid import UUID

import asyncpg

from spammers.common.db import create_pool

if TYPE_CHECKING:  # avoid import cycle; gateway imports state
    from spammers.signal.gateway import ReceiveDispatcher, SessionHub


@dataclass
class SignalMockState:
    pool: asyncpg.Pool
    run_id: UUID
    hub: "SessionHub"
    dispatcher: "Optional[ReceiveDispatcher]" = field(default=None)
    # Mock-only knob: arm a one-shot rate-limit on the next read (the server-driven
    # backpressure the flow doc §5.2 maps to signal_api_rate_limited + retry_after;
    # see /_control). The value is the server-chosen retry-after in seconds.
    rate_limit_seconds: Optional[int] = field(default=None)


_STATE: Optional[SignalMockState] = None


async def startup(run_id: UUID | None = None) -> SignalMockState:
    global _STATE
    if _STATE is not None:
        return _STATE
    from spammers.signal.gateway import SessionHub

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
    _STATE = SignalMockState(pool=pool, run_id=rid, hub=SessionHub())
    return _STATE


async def shutdown() -> None:
    global _STATE
    if _STATE is not None:
        if _STATE.dispatcher is not None:
            await _STATE.dispatcher.stop()
        await _STATE.hub.close_all()
        await _STATE.pool.close()
        _STATE = None


def state() -> SignalMockState:
    if _STATE is None:
        raise RuntimeError("signal mock state not initialised — call startup() first")
    return _STATE


async def install_for_run(pool: asyncpg.Pool, run_id: UUID) -> Optional[asyncpg.Record]:
    """Return this run's Signal install row, or None if not provisioned yet.

    Only an enabled install (``disabled_at IS NULL``) is selectable — the
    revocation chokepoint the flow doc §9 names (Fyralis never auto-flips it, a
    logged divergence, but the column gates selection here for fidelity)."""
    return await pool.fetchrow(
        "SELECT id, account_label, session_string, account_number, account_uuid, "
        "account_username FROM app_signal.installations "
        "WHERE run_id = $1 AND disabled_at IS NULL",
        run_id,
    )
