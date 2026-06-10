"""Signal mock — FastAPI app factory.

Usage:
    python -m spammers.signal run --port 7025

Reproduces the signal-cli linked-device METHOD contract over a transport
substitution (HTTP for the request/response reads, a WS gateway for the live
receive stream — the Discord/Telegram-gateway analog; Signal is "cloned from
Telegram"). The HTTP surface mirrors the high-level ``SignalClient`` calls a
backfill makes, carrying the REAL signal-cli envelope shapes:

    POST /v1/get_history        backward offset_ts paging (0=newest; ts < offset_ts;
                                min_ts EXCLUSIVE floor; <=100/page; newest-first; the
                                client computes next_offset = MIN ts of page +
                                is_last = short page). Each message is a genuine
                                signal-cli envelope (dataMessage / syncMessage).
    POST /v1/iter_threads       enumerate the linked account's threads (direct + group)
    POST /v1/has_history_since  the reconciler 1-row gap probe
    POST /v1/me                 the linked-account identity (connectivity + cred probe)

NB: real signal-cli has NO backward-history method at all — it is forward-only
(`receive`/`subscribeReceive`); `get_history`/`iter_threads`/`has_history_since`
are the Fyralis SignalClient CONTRACT (the part §11.2 confirms is real), which the
mock serves over this shim. That, and the HTTP+WS-vs-JSON-RPC-socket substitution,
are the two logged divergences.

Every read presents the persisted linked-device session credential (Fyralis
presets ``spam-signal``). A missing/wrong session → 401 signal_api_unauthorized.
An unknown thread → signal_api_error (JSON-RPC -32602). ``POST /_control/rate_limit``
arms a one-shot rate-limit (HTTP 429 + signal_api_rate_limited + server retry_after
— the server-driven backpressure the flow doc §5.2 maps). The lifespan boots state
+ starts the ``ReceiveDispatcher`` (the background task that turns ``signal.message``
timeline events into ``receive`` notification pushes).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from spammers.signal import auth as _auth
from spammers.signal import dto as _dto
from spammers.signal import state as _state
from spammers.signal.gateway import ReceiveDispatcher, gateway_endpoint

_MAX_PAGE = 100  # get_history caps a single backward page at 100 (SIGNAL_BACKFILL_PAGE_SIZE)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    st = await _state.startup()
    st.dispatcher = ReceiveDispatcher(st.pool, st.run_id, st.hub)
    st.dispatcher.start()
    yield
    await _state.shutdown()


def _err(*, http_code: int, jsonrpc_code: int, signal_code: str, message: str,
         retry_after: Optional[int] = None) -> JSONResponse:
    body = _dto.rpc_error(http_code=http_code, jsonrpc_code=jsonrpc_code,
                          signal_code=signal_code, message=message,
                          retry_after=retry_after)
    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = str(int(retry_after))
    return JSONResponse(body, status_code=http_code, headers=headers)


def _unauthorized() -> JSONResponse:
    return _err(http_code=401, jsonrpc_code=_dto.JSONRPC_INTERNAL_ERROR,
                signal_code="signal_api_unauthorized", message="signal_api_unauthorized")


async def _require_install(request: Request):
    """Return (install_row, None) on a valid session, else (None, error_response)."""
    st = _state.state()
    inst = await _state.install_for_run(st.pool, st.run_id)
    if inst is None:
        return None, _unauthorized()
    presented = _auth.extract_session(
        authorization=request.headers.get("authorization"),
        x_signal_session=request.headers.get("x-signal-session"))
    if not _auth.session_ok(presented, inst["session_string"]):
        return None, _unauthorized()
    return inst, None


async def _body(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _take_rate_limit() -> Optional[int]:
    """Consume the one-shot rate-limit knob (None if not armed)."""
    st = _state.state()
    secs = st.rate_limit_seconds
    if secs is not None:
        st.rate_limit_seconds = None
    return secs


async def _resolve_thread(pool, install_pk, peer: dict[str, Any]):
    """Resolve a thread by its {thread_id} (uuid|base64 groupId). None if unknown."""
    tid = peer.get("thread_id")
    if tid is None:
        return None
    return await pool.fetchrow(
        "SELECT id, thread_id, thread_kind, thread_title "
        "FROM app_signal.threads WHERE install_pk = $1 AND thread_id = $2",
        install_pk, str(tid))


def _message_envelope(row, thread, inst) -> dict[str, Any]:
    """Build the signal-cli envelope for one stored message row."""
    return _dto.envelope(
        ts_ms=int(row["ts_ms"]), body=row["body"], out=bool(row["out"]),
        self_number=inst["account_number"], self_uuid=inst["account_uuid"],
        sender_uuid=row["sender_uuid"], sender_number=row["sender_number"],
        sender_name=row["sender_name"], thread_kind=thread["thread_kind"],
        group_id=(thread["thread_id"] if thread["thread_kind"] == "group" else None),
        group_name=(thread["thread_title"] if thread["thread_kind"] == "group" else None),
        group_revision=row["group_revision"],
        # a direct self-sent (out=True) message's destination is the contact (the
        # thread uuid); signal-cli increasingly uses uuid-only, so number may be null.
        direct_peer_uuid=(thread["thread_id"] if thread["thread_kind"] == "direct" else None),
        direct_peer_number=None)


def create_app() -> FastAPI:
    app = FastAPI(title="Signal mock", lifespan=_lifespan)

    @app.post("/v1/get_history")
    async def get_history(request: Request):  # noqa: ANN202
        inst, err = await _require_install(request)
        if err is not None:
            return err
        secs = _take_rate_limit()
        if secs is not None:
            return _err(http_code=429, jsonrpc_code=_dto.JSONRPC_INTERNAL_ERROR,
                        signal_code="signal_api_rate_limited",
                        message="signal_api_rate_limited", retry_after=secs)

        st = _state.state()
        body = await _body(request)
        peer = body.get("thread") or {}
        thread = await _resolve_thread(st.pool, inst["id"], peer)
        if thread is None:
            return _err(http_code=400, jsonrpc_code=_dto.JSONRPC_INVALID_PARAMS,
                        signal_code="signal_api_error",
                        message="no such thread for this linked account")

        offset_ts = int(body.get("offset_ts") or 0)
        min_ts = int(body.get("min_ts") or 0)
        limit = body.get("limit")
        limit = _MAX_PAGE if limit is None else max(1, min(_MAX_PAGE, int(limit)))

        # Candidates: older than offset_ts (0=newest), strictly newer than min_ts.
        # Newest-first (descending ts_ms = the Signal message timestamp/id).
        rows = await st.pool.fetch(
            """
            SELECT ts_ms, sender_uuid, sender_number, sender_name, body, out,
                   group_revision
              FROM app_signal.messages
             WHERE thread_pk = $1
               AND ($2::bigint = 0 OR ts_ms < $2::bigint)
               AND ts_ms > $3::bigint
             ORDER BY ts_ms DESC
             LIMIT $4
            """,
            thread["id"], offset_ts, min_ts, limit)
        messages = [_message_envelope(r, thread, inst) for r in rows]
        total = await st.pool.fetchval(
            "SELECT COUNT(*) FROM app_signal.messages WHERE thread_pk = $1",
            thread["id"])
        return JSONResponse({"messages": messages, "count": int(total)})

    @app.post("/v1/iter_threads")
    async def iter_threads(request: Request):  # noqa: ANN202
        inst, err = await _require_install(request)
        if err is not None:
            return err
        st = _state.state()
        rows = await st.pool.fetch(
            "SELECT thread_id, thread_kind, thread_title "
            "FROM app_signal.threads WHERE install_pk = $1 ORDER BY thread_id ASC",
            inst["id"])
        threads = [
            _dto.thread_descriptor(thread_id=r["thread_id"], thread_kind=r["thread_kind"],
                                   thread_title=r["thread_title"])
            for r in rows
        ]
        return JSONResponse({"threads": threads, "count": len(threads)})

    @app.post("/v1/has_history_since")
    async def has_history_since(request: Request):  # noqa: ANN202
        inst, err = await _require_install(request)
        if err is not None:
            return err
        st = _state.state()
        body = await _body(request)
        peer = body.get("thread") or {}
        thread = await _resolve_thread(st.pool, inst["id"], peer)
        if thread is None:
            return _err(http_code=400, jsonrpc_code=_dto.JSONRPC_INVALID_PARAMS,
                        signal_code="signal_api_error",
                        message="no such thread for this linked account")
        min_ts = int(body.get("min_ts") or 0)
        newest = await st.pool.fetchval(
            "SELECT MAX(ts_ms) FROM app_signal.messages "
            "WHERE thread_pk = $1 AND ts_ms > $2::bigint", thread["id"], min_ts)
        return JSONResponse({"has_more": newest is not None,
                             "newest_ts": int(newest) if newest is not None else None})

    @app.post("/v1/me")
    async def me(request: Request):  # noqa: ANN202
        inst, err = await _require_install(request)
        if err is not None:
            return err
        return JSONResponse({"account": _dto.account_dto(
            number=inst["account_number"], uuid=inst["account_uuid"],
            username=inst["account_username"])})

    app.add_api_websocket_route("/gateway", gateway_endpoint)

    # ---- mock-only control knobs (deterministic failure paths) ----
    @app.post("/_control/rate_limit")
    async def control_rate_limit(request: Request):  # noqa: ANN202
        body = await _body(request)
        _state.state().rate_limit_seconds = int(body.get("seconds", 30))
        return {"ok": True, "rate_limit_seconds": _state.state().rate_limit_seconds}

    @app.post("/_control/reset")
    async def control_reset():  # noqa: ANN202
        _state.state().rate_limit_seconds = None
        return {"ok": True}

    @app.get("/_health")
    async def health():  # noqa: ANN202
        return {"ok": True, "service": "signal-mock"}

    return app


app = create_app()
