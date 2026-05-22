"""The Gateway WebSocket endpoint.

Lifecycle of one connection:

    accept → HELLO(10)
    (pre-auth) client may HEARTBEAT(1); must then IDENTIFY(2) or RESUME(6)
      IDENTIFY → validate token/intents → READY(0) + GUILD_CREATE(0) per guild
      RESUME   → replay buffered dispatches → RESUMED(0)
    (authed) reader handles HEARTBEAT/PRESENCE/etc.; dispatcher pushes events;
             heartbeat monitor closes 4009 on silence; on disconnect the session
             is parked for RESUME.

Concurrency: exactly one **writer** task touches ``websocket.send``; it drains
the session's outbound queue (and honors out-of-band close requests). A
**reader** task handles inbound frames (enqueuing responses, never sending
directly). A **heartbeat monitor** trips a 4009 close on missed heartbeats.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
from typing import Any, Optional

import structlog
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from spammers.discord.dto import (
    bot_user_dto,
    channel_dto,
    guild_dto,
    unavailable_guild_dto,
)
from spammers.discord.gateway.opcodes import CloseCode, Intents, Op
from spammers.discord.gateway import protocol
from spammers.discord.gateway.session import GatewaySession
from spammers.discord.gateway_url import gateway_ws_base
from spammers.discord.state import state

log = structlog.get_logger("spammers.discord.gateway")

# Heartbeat interval the mock advertises (ms). Module-level so tests can shrink
# it for fast, deterministic heartbeat-timeout checks.
heartbeat_interval_ms: int = 41250
# Multiplier on the interval before a silent client is declared dead (4009).
HEARTBEAT_TOLERANCE = 1.5

# Intents the mock refuses (4014). Empty by default (all approved); tests/config
# may set bits to exercise the disallowed-intents close.
disallowed_intents: int = 0

GATEWAY_VERSION = 10


async def gateway_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()

    # JSON encoding only — ETF closes with a decode error, like real Discord.
    if websocket.query_params.get("encoding", "json") not in ("json", ""):
        await websocket.close(code=CloseCode.DECODE_ERROR.value)
        return

    await websocket.send_json(protocol.hello(heartbeat_interval_ms))

    # ---- pre-auth: HEARTBEAT allowed; expect IDENTIFY or RESUME ----
    session: Optional[GatewaySession] = None
    replay_after: Optional[int] = None
    try:
        while session is None:
            frame = await _receive_frame(websocket)
            op = frame.get("op")
            if op == Op.HEARTBEAT.value:
                await websocket.send_json(protocol.heartbeat_ack())
                continue
            if op == Op.IDENTIFY.value:
                session = await _handle_identify(websocket, frame.get("d") or {})
                if session is None:
                    return  # _handle_identify already closed the socket
            elif op == Op.RESUME.value:
                resumed, replay_after = await _handle_resume(websocket, frame.get("d") or {})
                session = resumed
                if session is None:
                    return
            else:
                await websocket.close(code=CloseCode.NOT_AUTHENTICATED.value)
                return
    except WebSocketDisconnect:
        return
    except _DecodeError:
        await websocket.close(code=CloseCode.DECODE_ERROR.value)
        return

    await _run_authed(websocket, session, replay_after)


# --------------------------------------------------------------------------- #
# IDENTIFY / RESUME
# --------------------------------------------------------------------------- #

async def _handle_identify(websocket: WebSocket, d: dict[str, Any]) -> Optional[GatewaySession]:
    token = _strip_token(d.get("token"))
    intents = int(d.get("intents", 0))

    if intents & ~Intents.ALL_KNOWN:
        await websocket.close(code=CloseCode.INVALID_INTENTS.value)
        return None
    if disallowed_intents and (intents & disallowed_intents):
        await websocket.close(code=CloseCode.DISALLOWED_INTENTS.value)
        return None

    app = await _resolve_app_by_token(token)
    if app is None:
        await websocket.close(code=CloseCode.AUTHENTICATION_FAILED.value)
        return None

    session = GatewaySession(
        session_id=secrets.token_hex(16),
        token=token,
        application_pk=app["application_pk"],
        application_id=app["application_id"],
        intents=intents,
    )
    state().hub.register(session)
    return session


async def _handle_resume(
    websocket: WebSocket, d: dict[str, Any]
) -> tuple[Optional[GatewaySession], Optional[int]]:
    token = _strip_token(d.get("token"))
    session_id = d.get("session_id") or ""
    seq = d.get("seq")

    hub = state().hub
    session = hub.take_resumable(session_id)
    if session is None:
        # Unknown / expired session — client must start over.
        await websocket.send_json(protocol.invalid_session(False))
        await websocket.close(code=CloseCode.INVALID_SEQ.value)
        return None, None
    if session.token != token:
        await websocket.close(code=CloseCode.AUTHENTICATION_FAILED.value)
        return None, None

    oldest = session.oldest_buffered_seq()
    if not isinstance(seq, int) or (oldest is not None and seq < oldest - 1):
        # Gap too large to replay — invalidate (non-resumable).
        await websocket.send_json(protocol.invalid_session(False))
        await websocket.close(code=CloseCode.INVALID_SEQ.value)
        return None, None

    session.rebind()
    hub.register(session)
    return session, seq


# --------------------------------------------------------------------------- #
# Authenticated phase
# --------------------------------------------------------------------------- #

async def _run_authed(
    websocket: WebSocket, session: GatewaySession, replay_after: Optional[int]
) -> None:
    if replay_after is not None:
        # RESUME: replay missed dispatches with their original seq, then RESUMED.
        for payload in session.frames_after(replay_after):
            session.enqueue_buffered(payload)
        session.dispatch("RESUMED", {})
    else:
        # Fresh IDENTIFY: READY then a GUILD_CREATE per guild.
        ready_d, guild_creates = await _build_ready(session)
        session.dispatch("READY", ready_d)
        for gc in guild_creates:
            session.dispatch("GUILD_CREATE", gc)

    writer = asyncio.ensure_future(_writer_task(websocket, session))
    reader = asyncio.ensure_future(_reader_task(websocket, session))
    monitor = asyncio.ensure_future(_heartbeat_monitor(session))
    try:
        done, pending = await asyncio.wait(
            {writer, reader, monitor}, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        for task in (writer, reader, monitor):
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.gather(writer, reader, monitor, return_exceptions=True)
        # Park for RESUME (retains the ring buffer); a clean close drops it.
        state().hub.park(session)
        if websocket.client_state != WebSocketState.DISCONNECTED:
            with contextlib.suppress(Exception):
                await websocket.close(code=session.close_code or CloseCode.UNKNOWN_ERROR.value)


async def _writer_task(websocket: WebSocket, session: GatewaySession) -> None:
    """Sole owner of ``websocket.send``. Drains the queue; honors close requests."""
    close_task = asyncio.ensure_future(session.close_requested.wait())
    get_task: Optional[asyncio.Future] = None
    try:
        while True:
            get_task = asyncio.ensure_future(session.out.get())
            done, _pending = await asyncio.wait(
                {get_task, close_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if get_task in done:
                payload = get_task.result()
                try:
                    await websocket.send_json(payload)
                except (WebSocketDisconnect, RuntimeError):
                    return
                continue
            # close requested
            with contextlib.suppress(Exception):
                await websocket.close(code=session.close_code or CloseCode.UNKNOWN_ERROR.value)
            return
    finally:
        for task in (get_task, close_task):
            if task is not None and not task.done():
                task.cancel()


async def _reader_task(websocket: WebSocket, session: GatewaySession) -> None:
    """Handle inbound frames; enqueue responses (never send directly)."""
    while True:
        try:
            frame = await _receive_frame(websocket)
        except WebSocketDisconnect:
            return
        except _DecodeError:
            session.request_close(CloseCode.DECODE_ERROR.value)
            return

        op = frame.get("op")
        if op == Op.HEARTBEAT.value:
            session.touch_heartbeat()
            _safe_enqueue(session, protocol.heartbeat_ack())
        elif op == Op.IDENTIFY.value:
            session.request_close(CloseCode.ALREADY_AUTHENTICATED.value)
            return
        elif op in (Op.PRESENCE_UPDATE.value, Op.VOICE_STATE.value, Op.REQUEST_GUILD_MEMBERS.value):
            continue  # accepted, no-op — connection stays open
        elif op == Op.RESUME.value:
            continue  # already on a live session; ignore
        else:
            session.request_close(CloseCode.UNKNOWN_OPCODE.value)
            return


async def _heartbeat_monitor(session: GatewaySession) -> None:
    deadline_s = (heartbeat_interval_ms / 1000.0) * HEARTBEAT_TOLERANCE
    check_every = max(0.02, deadline_s / 4)
    loop = asyncio.get_event_loop()
    while True:
        await asyncio.sleep(check_every)
        if loop.time() - session.last_heartbeat > deadline_s:
            session.request_close(CloseCode.SESSION_TIMED_OUT.value)
            return


# --------------------------------------------------------------------------- #
# READY / GUILD_CREATE construction
# --------------------------------------------------------------------------- #

async def _build_ready(session: GatewaySession) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    st = state()
    guilds = await st.pool.fetch(
        "SELECT id, guild_id, name, icon_hash, owner_user_id, created_at "
        "FROM app_discord.guilds WHERE application_pk = $1 ORDER BY created_at",
        session.application_pk,
    )
    ready = {
        "v": GATEWAY_VERSION,
        "user": bot_user_dto(session.application_id),
        "guilds": [unavailable_guild_dto(g["guild_id"]) for g in guilds],
        "session_id": session.session_id,
        "resume_gateway_url": gateway_ws_base(),
        "application": {"id": session.application_id, "flags": 0},
        "shard": [0, 1],
    }
    guild_creates: list[dict[str, Any]] = []
    for g in guilds:
        chans = await st.pool.fetch(
            "SELECT channel_id, name, type, parent_id, topic, nsfw "
            "FROM app_discord.channels WHERE guild_pk = $1 ORDER BY created_at",
            g["id"],
        )
        gd = guild_dto(dict(g))
        gd.update({
            "joined_at": _iso(g["created_at"]),
            "large": False,
            "unavailable": False,
            "member_count": 0,
            "channels": [channel_dto(dict(c), guild_id=g["guild_id"]) for c in chans],
            "members": [],
            "voice_states": [],
            "presences": [],
        })
        guild_creates.append(gd)
    return ready, guild_creates


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

class _DecodeError(Exception):
    pass


def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt is not None else None


async def _receive_frame(websocket: WebSocket) -> dict[str, Any]:
    """Receive one text frame as a JSON object. Raises ``_DecodeError`` on bad
    JSON and ``WebSocketDisconnect`` on close."""
    text = await websocket.receive_text()
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        raise _DecodeError()
    if not isinstance(obj, dict):
        raise _DecodeError()
    return obj


def _safe_enqueue(session: GatewaySession, payload: dict[str, Any]) -> None:
    try:
        session.enqueue(payload)
    except asyncio.QueueFull:
        session.request_close(CloseCode.SESSION_TIMED_OUT.value)


def _strip_token(token: Any) -> str:
    if not isinstance(token, str):
        return ""
    t = token.strip()
    if t.lower().startswith("bot "):
        return t[4:].strip()
    return t


async def _resolve_app_by_token(token: str) -> Optional[dict]:
    if not token:
        return None
    st = state()
    row = await st.pool.fetchrow(
        "SELECT id AS application_pk, application_id FROM app_discord.applications "
        "WHERE run_id = $1 AND bot_token = $2 LIMIT 1",
        st.run_id, token,
    )
    return dict(row) if row else None
