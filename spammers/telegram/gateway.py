"""The Telegram live updates gateway — the persistent MTProto updates connection.

The flow doc models Telegram's live surface as "a gateway source like Discord": a
single long-lived connection over which the server PUSHES ``updateNewMessage`` /
``updateEditMessage`` (no HTTP webhook, no HMAC — the authenticated connection is
the trust boundary). We reproduce that with a WebSocket the consumer connects to:

    connect (?session=<s>  or first frame {"session": <s>})
      → server validates the session → sends ``updates.state`` (pts/qts/seq/date)
      → server pushes ``updateNewMessage`` / ``updateEditMessage`` frames
    a bad/missing session → rpc_error(AUTH_KEY_UNREGISTERED) + close(4401)
      (the gateway analog of a webhook-tamper rejection)

The mock owns this (the WebSockets live in this process, like Discord). The
``UpdatesDispatcher`` polls ``telegram.message`` timeline events, projects each
into ``app_telegram.messages`` (so a later backfill sees the same row → cross-path
dedup on the edit-versioned external_id) and fans the update out to the install's
connected sessions. A watermark at startup means messages at/before the
dispatcher's start time are projected + marked but never pushed (a client
connecting late does not get a flood of past messages) — matching real Telegram
(history comes from ``messages.getHistory``, not the updates stream).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

import asyncpg
import structlog
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from spammers.common.clock import get_clock
from spammers.telegram import auth as _auth
from spammers.telegram import dto as _dto
from spammers.telegram import state as _state

log = structlog.get_logger("spammers.telegram.gateway")

# WS close codes (4000-range, app-defined like Discord's).
CLOSE_AUTH_FAILED = 4401
CLOSE_SERVER_SHUTDOWN = 4000


class GatewaySession:
    """One connected updates connection for an install."""

    def __init__(self, *, session_id: str, install_pk: UUID) -> None:
        self.session_id = session_id
        self.install_pk = install_pk
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=1024)
        self.connected = True

    def push(self, frame: dict[str, Any]) -> None:
        self.queue.put_nowait(frame)  # raises QueueFull → caller drops the session


class SessionHub:
    """Registry of live updates connections + the fan-out path."""

    def __init__(self) -> None:
        self.live: dict[str, GatewaySession] = {}

    def register(self, session: GatewaySession) -> None:
        self.live[session.session_id] = session

    def drop(self, session_id: str) -> None:
        self.live.pop(session_id, None)

    def fan_out(self, install_pk: UUID, frame: dict[str, Any]) -> int:
        """Push ``frame`` to every live session of ``install_pk``. A full queue
        drops that one session. Returns the count delivered."""
        delivered = 0
        for session in list(self.live.values()):
            if session.install_pk != install_pk:
                continue
            try:
                session.push(frame)
            except asyncio.QueueFull:
                log.warning("telegram_fanout_drop", session_id=session.session_id)
                session.connected = False
                self.live.pop(session.session_id, None)
                continue
            delivered += 1
        return delivered

    async def close_all(self) -> None:
        for s in list(self.live.values()):
            s.connected = False
        self.live.clear()


_CLAIM_SQL = """
    UPDATE timeline.events SET emitted_at = now()
     WHERE id IN (
        SELECT id FROM timeline.events
         WHERE run_id = $1
           AND type = 'telegram.message'
           AND is_historical = FALSE
           AND emitted_at IS NULL
           AND virtual_ts <= $2
         ORDER BY virtual_ts ASC
         LIMIT $3
         FOR UPDATE SKIP LOCKED
     )
    RETURNING id, payload, virtual_ts
"""


class UpdatesDispatcher:
    """Turns ``telegram.message`` timeline events into update pushes."""

    def __init__(self, pool: asyncpg.Pool, run_id: UUID, hub: SessionHub, *,
                 poll_interval_s: float = 0.5, batch_size: int = 20) -> None:
        self._pool = pool
        self._run_id = run_id
        self._hub = hub
        self._poll_interval_s = poll_interval_s
        self._batch_size = batch_size
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._watermark: Optional[datetime] = None
        self._pts = 0  # per-install would be ideal; one install/run so a single counter

    async def _drain_once(self) -> int:
        clock = await get_clock(self._pool, self._run_id)
        if self._watermark is None:
            self._watermark = clock.virtual_now  # no replay of pre-start messages
        rows = await self._pool.fetch(
            _CLAIM_SQL, self._run_id, clock.virtual_now, self._batch_size)
        for row in rows:
            try:
                await self._handle(row)
            except Exception as exc:  # never let one bad event stall the loop
                log.warning("telegram_dispatch_failed", event_id=str(row["id"]),
                            error=str(exc))
        return len(rows)

    async def _handle(self, row: asyncpg.Record) -> None:
        payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
        virtual_ts: datetime = row["virtual_ts"]
        if virtual_ts.tzinfo is None:
            virtual_ts = virtual_ts.replace(tzinfo=timezone.utc)
        date_ts = int(virtual_ts.timestamp())

        inst = await self._pool.fetchrow(
            "SELECT id FROM app_telegram.installations WHERE run_id = $1", self._run_id)
        if inst is None:
            log.warning("telegram_dispatch_no_install")
            return
        dialog = await self._pool.fetchrow(
            "SELECT id, dialog_id, dialog_kind, access_hash, title "
            "FROM app_telegram.dialogs WHERE install_pk = $1 AND dialog_id = $2",
            inst["id"], int(payload["dialog_id"]))
        if dialog is None:
            log.warning("telegram_dispatch_no_dialog", dialog_id=payload.get("dialog_id"))
            return

        kind = payload.get("kind", "new")
        from_user_id = payload.get("from_user_id")
        text = payload.get("text", "") or ""

        if kind == "edit":
            # Edit the referenced message (or the newest) → fresh edit_date, same id.
            target = payload.get("edit_message_id")
            if target is not None:
                msg = await self._pool.fetchrow(
                    "SELECT id, message_id, date_ts, out, from_user_id "
                    "FROM app_telegram.messages WHERE dialog_pk = $1 AND message_id = $2",
                    dialog["id"], int(target))
            else:
                msg = await self._pool.fetchrow(
                    "SELECT id, message_id, date_ts, out, from_user_id "
                    "FROM app_telegram.messages WHERE dialog_pk = $1 "
                    "ORDER BY message_id DESC LIMIT 1", dialog["id"])
            if msg is None:
                return
            await self._pool.execute(
                "UPDATE app_telegram.messages SET edit_date_ts = $2, text = $3, "
                "is_historical = FALSE, timeline_event_id = $4 WHERE id = $1",
                msg["id"], date_ts, text, row["id"])
            message = _dto.message_dto(
                message_id=msg["message_id"], dialog_id=dialog["dialog_id"],
                dialog_kind=dialog["dialog_kind"], date_ts=msg["date_ts"],
                edit_date_ts=date_ts, text=text, out=msg["out"],
                from_user_id=msg["from_user_id"])
            self._pts += 1
            frame = _dto.update_edit_message(
                message=message, pts=self._pts,
                dialog=self._dialog_ctx(dialog))
        else:
            next_id = await self._pool.fetchval(
                "SELECT COALESCE(MAX(message_id), 0) + 1 FROM app_telegram.messages "
                "WHERE dialog_pk = $1", dialog["id"])
            out = bool(payload.get("out", False))
            await self._pool.execute(
                """INSERT INTO app_telegram.messages
                    (id, dialog_pk, message_id, date_ts, edit_date_ts, text, out,
                     from_user_id, created_at, is_historical, timeline_event_id)
                   VALUES ($1,$2,$3,$4,NULL,$5,$6,$7,$8,FALSE,$9)""",
                _uuid(), dialog["id"], int(next_id), date_ts, text, out,
                from_user_id, virtual_ts, row["id"])
            message = _dto.message_dto(
                message_id=int(next_id), dialog_id=dialog["dialog_id"],
                dialog_kind=dialog["dialog_kind"], date_ts=date_ts,
                edit_date_ts=None, text=text, out=out, from_user_id=from_user_id)
            self._pts += 1
            frame = _dto.update_new_message(
                message=message, pts=self._pts,
                dialog=self._dialog_ctx(dialog))

        # No historical replay: only push events strictly after the watermark.
        if self._watermark is not None and virtual_ts <= self._watermark:
            return
        self._hub.fan_out(inst["id"], frame)

    @staticmethod
    def _dialog_ctx(dialog: asyncpg.Record) -> dict[str, Any]:
        return {"dialog_id": dialog["dialog_id"], "dialog_kind": dialog["dialog_kind"],
                "title": dialog["title"]}

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                n = await self._drain_once()
                if n == 0:
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(self._stop.wait(),
                                               timeout=self._poll_interval_s)
            except Exception as exc:
                log.warning("telegram_dispatcher_loop_error", error=str(exc))
                await asyncio.sleep(self._poll_interval_s)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None


def _uuid() -> UUID:
    return UUID(bytes=secrets.token_bytes(16), version=4)


async def gateway_endpoint(websocket: WebSocket) -> None:
    """The persistent updates connection. Auth with the session, then receive
    pushed update frames until disconnect."""
    await websocket.accept()
    st = _state.state()

    inst = await _state.install_for_run(st.pool, st.run_id)
    if inst is None:
        await websocket.send_json(_dto.rpc_error(401, "AUTH_KEY_UNREGISTERED"))
        await websocket.close(code=CLOSE_AUTH_FAILED)
        return

    # Resolve the session credential: ?session= query param, or the first frame.
    presented = _auth.extract_session(
        authorization=websocket.headers.get("authorization"),
        x_telegram_session=websocket.headers.get("x-telegram-session"),
        query_session=websocket.query_params.get("session"))
    if presented is None:
        try:
            first = await asyncio.wait_for(websocket.receive_text(), timeout=10)
            presented = (json.loads(first) or {}).get("session")
        except (asyncio.TimeoutError, WebSocketDisconnect, ValueError, TypeError):
            presented = None

    if not _auth.session_ok(presented, inst["session_string"]):
        await websocket.send_json(_dto.rpc_error(401, "AUTH_KEY_UNREGISTERED"))
        await websocket.close(code=CLOSE_AUTH_FAILED)
        return

    session = GatewaySession(session_id=secrets.token_hex(8), install_pk=inst["id"])
    st.hub.register(session)

    # The get_state ack (the live update-state cursor the consumer warm-starts on).
    clock = await get_clock(st.pool, st.run_id)
    now = clock.virtual_now
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    await websocket.send_json({
        "_": "updates.state",
        "pts": 0, "qts": 0, "seq": 0,
        "date": int(now.timestamp()),
        "user_id": inst["self_user_id"],
    })

    async def _writer() -> None:
        while session.connected:
            try:
                frame = await asyncio.wait_for(session.queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if websocket.application_state != WebSocketState.CONNECTED:
                break
            await websocket.send_json(frame)

    async def _reader() -> None:
        # We don't expect inbound frames after auth; the reader exists only to
        # observe the client disconnecting.
        try:
            while session.connected:
                await websocket.receive_text()
        except WebSocketDisconnect:
            return

    writer = asyncio.create_task(_writer())
    reader = asyncio.create_task(_reader())
    try:
        await asyncio.wait({writer, reader}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        session.connected = False
        st.hub.drop(session.session_id)
        for t in (writer, reader):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
        if websocket.application_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await websocket.close(code=CLOSE_SERVER_SHUTDOWN)
