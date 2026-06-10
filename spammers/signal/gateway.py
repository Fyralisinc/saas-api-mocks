"""The Signal live receive gateway — the persistent linked-device receive loop.

The flow doc models Signal's live surface as a gateway "exactly like Telegram":
a single long-lived authenticated linked-device connection over which the daemon
STREAMS each incoming message (no HTTP webhook, no HMAC — the authenticated
session is the trust boundary). We reproduce that with a WebSocket the consumer
connects to, and PUSH genuine signal-cli ``receive`` JSON-RPC notifications:

    connect (?session=<s>  or first frame {"session": <s>})
      → server validates the session → sends a ``subscribed`` ack
      → server pushes ``{"jsonrpc":"2.0","method":"receive","params":{"envelope":…}}``
    a bad/missing session → rpc_error(signal_api_unauthorized) + close(4401)
      (the gateway analog of a webhook-tamper rejection)

The mock owns this (the WebSockets live in this process, like Discord/Telegram).
The ``ReceiveDispatcher`` polls ``signal.message`` timeline events, projects each
into ``app_signal.messages`` (so a later backfill sees the same row → cross-path
dedup on the install-namespaced external_id) and fans out a ``receive``
notification to the install's connected sessions. The linked account's OWN
outgoing messages (``out`` True — a ``syncMessage.sentMessage``) are projected for
backfill parity but SKIPPED on the live fan-out (flow doc §7.3). A watermark at
startup means messages at/before the dispatcher's start time are projected + marked
but never pushed (a client connecting late does not get a flood of past messages).
Signal v1 has NO edits → there is no edit-message push (contrast Telegram).
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
from spammers.signal import auth as _auth
from spammers.signal import dto as _dto
from spammers.signal import state as _state

log = structlog.get_logger("spammers.signal.gateway")

# WS close codes (4000-range, app-defined like Discord/Telegram).
CLOSE_AUTH_FAILED = 4401
CLOSE_SERVER_SHUTDOWN = 4000


class GatewaySession:
    """One connected receive connection for an install."""

    def __init__(self, *, session_id: str, install_pk: UUID) -> None:
        self.session_id = session_id
        self.install_pk = install_pk
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=1024)
        self.connected = True

    def push(self, frame: dict[str, Any]) -> None:
        self.queue.put_nowait(frame)  # raises QueueFull → caller drops the session


class SessionHub:
    """Registry of live receive connections + the fan-out path."""

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
                log.warning("signal_fanout_drop", session_id=session.session_id)
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
           AND type = 'signal.message'
           AND is_historical = FALSE
           AND emitted_at IS NULL
           AND virtual_ts <= $2
         ORDER BY virtual_ts ASC
         LIMIT $3
         FOR UPDATE SKIP LOCKED
     )
    RETURNING id, payload, virtual_ts
"""


class ReceiveDispatcher:
    """Turns ``signal.message`` timeline events into ``receive`` notification pushes."""

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
                log.warning("signal_dispatch_failed", event_id=str(row["id"]),
                            error=str(exc))
        return len(rows)

    async def _handle(self, row: asyncpg.Record) -> None:
        payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
        virtual_ts: datetime = row["virtual_ts"]
        if virtual_ts.tzinfo is None:
            virtual_ts = virtual_ts.replace(tzinfo=timezone.utc)

        inst = await self._pool.fetchrow(
            "SELECT id, account_number, account_uuid FROM app_signal.installations "
            "WHERE run_id = $1 AND disabled_at IS NULL", self._run_id)
        if inst is None:
            log.warning("signal_dispatch_no_install")
            return
        thread = await self._pool.fetchrow(
            "SELECT id, thread_id, thread_kind, thread_title "
            "FROM app_signal.threads WHERE install_pk = $1 AND thread_id = $2",
            inst["id"], str(payload["thread_id"]))
        if thread is None:
            log.warning("signal_dispatch_no_thread", thread_id=payload.get("thread_id"))
            return

        # The Signal message id IS the timestamp in MILLISECONDS. Use the event's
        # virtual time in ms, guaranteed newer-than-existing + unique within the
        # thread (the frozen-instant collision guard, like fireflies).
        ms = int(virtual_ts.timestamp() * 1000)
        max_existing = await self._pool.fetchval(
            "SELECT COALESCE(MAX(ts_ms), 0) FROM app_signal.messages WHERE thread_pk = $1",
            thread["id"])
        ts_ms = max(ms, int(max_existing) + 1)

        out = bool(payload.get("out", False))
        body = payload.get("body", "") or ""
        sender_uuid = payload.get("sender_uuid")
        sender_number = payload.get("sender_number")
        sender_name = payload.get("sender_name")
        group_revision = payload.get("group_revision") if thread["thread_kind"] == "group" else None

        await self._pool.execute(
            """INSERT INTO app_signal.messages
                (id, thread_pk, ts_ms, sender_uuid, sender_number, sender_name,
                 body, out, group_revision, created_at, is_historical, timeline_event_id)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,FALSE,$11)""",
            _uuid(), thread["id"], ts_ms, sender_uuid, sender_number, sender_name,
            body, out, group_revision, virtual_ts, row["id"])

        # The linked account's own outgoing messages (out=True) are projected for
        # backfill parity but SKIPPED on the live fan-out (flow doc §7.3).
        if out:
            return
        # No historical replay: only push events strictly after the watermark.
        if self._watermark is not None and virtual_ts <= self._watermark:
            return

        env = _dto.envelope(
            ts_ms=ts_ms, body=body, out=out,
            self_number=inst["account_number"], self_uuid=inst["account_uuid"],
            sender_uuid=sender_uuid, sender_number=sender_number, sender_name=sender_name,
            thread_kind=thread["thread_kind"],
            group_id=(thread["thread_id"] if thread["thread_kind"] == "group" else None),
            group_name=(thread["thread_title"] if thread["thread_kind"] == "group" else None),
            group_revision=group_revision,
            direct_peer_uuid=payload.get("direct_peer_uuid"),
            direct_peer_number=payload.get("direct_peer_number"))
        self._hub.fan_out(inst["id"], _receive_frame(env, inst["account_number"]))

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                n = await self._drain_once()
                if n == 0:
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(self._stop.wait(),
                                               timeout=self._poll_interval_s)
            except Exception as exc:
                log.warning("signal_dispatcher_loop_error", error=str(exc))
                await asyncio.sleep(self._poll_interval_s)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None


def _receive_frame(env: dict[str, Any], account_number: str) -> dict[str, Any]:
    """A signal-cli ``receive`` JSON-RPC notification (no id) wrapping one
    envelope — the exact shape the daemon pushes down the subscribed connection."""
    return {"jsonrpc": "2.0", "method": "receive",
            "params": {"envelope": env, "account": account_number, "subscription": 0}}


def _uuid() -> UUID:
    return UUID(bytes=secrets.token_bytes(16), version=4)


async def gateway_endpoint(websocket: WebSocket) -> None:
    """The persistent receive connection. Auth with the linked-device session,
    then receive pushed ``receive`` frames until disconnect."""
    await websocket.accept()
    st = _state.state()

    inst = await _state.install_for_run(st.pool, st.run_id)
    if inst is None:
        await websocket.send_json(_dto.rpc_error(
            http_code=401, jsonrpc_code=_dto.JSONRPC_INTERNAL_ERROR,
            signal_code="signal_api_unauthorized", message="signal_api_unauthorized"))
        await websocket.close(code=CLOSE_AUTH_FAILED)
        return

    # Resolve the session credential: ?session= query param, or the first frame.
    presented = _auth.extract_session(
        authorization=websocket.headers.get("authorization"),
        x_signal_session=websocket.headers.get("x-signal-session"),
        query_session=websocket.query_params.get("session"))
    if presented is None:
        try:
            first = await asyncio.wait_for(websocket.receive_text(), timeout=10)
            presented = (json.loads(first) or {}).get("session")
        except (asyncio.TimeoutError, WebSocketDisconnect, ValueError, TypeError):
            presented = None

    if not _auth.session_ok(presented, inst["session_string"]):
        await websocket.send_json(_dto.rpc_error(
            http_code=401, jsonrpc_code=_dto.JSONRPC_INTERNAL_ERROR,
            signal_code="signal_api_unauthorized", message="signal_api_unauthorized"))
        await websocket.close(code=CLOSE_AUTH_FAILED)
        return

    session = GatewaySession(session_id=secrets.token_hex(8), install_pk=inst["id"])
    st.hub.register(session)

    # The subscribeReceive ack (the live subscription the consumer warm-starts on).
    await websocket.send_json({
        "jsonrpc": "2.0", "method": "subscribed",
        "params": {"subscription": 0, "account": inst["account_number"],
                   "uuid": inst["account_uuid"]},
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
