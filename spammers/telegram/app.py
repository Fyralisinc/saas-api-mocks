"""Telegram mock — FastAPI app factory.

Usage:
    python -m spammers.telegram run --port 7024

Reproduces the MTProto user-account METHOD contract over a transport
substitution (HTTP for the request/response reads, a WS gateway for the live
updates push — the Discord-gateway analog the flow doc names). The HTTP surface
mirrors the Telethon high-level calls Fyralis's ``TelegramClient`` makes:

    POST /messages.getHistory   backward offset_id paging (0=newest; id < offset_id;
                                min_id EXCLUSIVE floor; max_id EXCLUSIVE; ≤100/page;
                                newest-first; the client computes next_offset_id = MIN
                                id of page and is_last = short page)
    POST /messages.getDialogs   enumerate dialogs (the iter_dialogs shape)
    POST /users.getFullUser     the get_me self user (connectivity + credential probe)

Every read presents the persisted session credential (the auth_key stand-in;
Fyralis presets ``spam-telegram``). A missing/wrong session → 401
AUTH_KEY_UNREGISTERED (the unauthorized/revoked analog). A bad peer →
PEER_ID_INVALID. ``POST /_control/flood_wait`` arms a one-shot FLOOD_WAIT (RPC
error 420 + server seconds — the protocol's own backpressure). The lifespan boots
state + starts the ``UpdatesDispatcher`` (the background task that turns
``telegram.message`` timeline events into ``updateNewMessage`` / ``updateEditMessage``
WS pushes).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from spammers.telegram import auth as _auth
from spammers.telegram import dto as _dto
from spammers.telegram import state as _state
from spammers.telegram.gateway import UpdatesDispatcher, gateway_endpoint

_MAX_PAGE = 100  # messages.getHistory caps a single page at 100 (Telethon _MAX_CHUNK_SIZE)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    st = await _state.startup()
    st.dispatcher = UpdatesDispatcher(st.pool, st.run_id, st.hub)
    st.dispatcher.start()
    yield
    await _state.shutdown()


def _err(code: int, message: str) -> JSONResponse:
    return JSONResponse(_dto.rpc_error(code, message), status_code=code)


async def _require_install(request: Request):
    """Return (install_row, None) on a valid session, else (None, error_response)."""
    st = _state.state()
    inst = await _state.install_for_run(st.pool, st.run_id)
    if inst is None:
        return None, _err(401, "AUTH_KEY_UNREGISTERED")
    presented = _auth.extract_session(
        authorization=request.headers.get("authorization"),
        x_telegram_session=request.headers.get("x-telegram-session"))
    if not _auth.session_ok(presented, inst["session_string"]):
        return None, _err(401, "AUTH_KEY_UNREGISTERED")
    return inst, None


async def _body(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _take_flood_wait() -> Optional[int]:
    """Consume the one-shot FLOOD_WAIT knob (None if not armed)."""
    st = _state.state()
    secs = st.flood_wait_seconds
    if secs is not None:
        st.flood_wait_seconds = None
    return secs


def create_app() -> FastAPI:
    app = FastAPI(title="Telegram mock", lifespan=_lifespan)

    @app.post("/messages.getHistory")
    async def get_history(request: Request):  # noqa: ANN202
        inst, err = await _require_install(request)
        if err is not None:
            return err
        secs = _take_flood_wait()
        if secs is not None:
            return _err(420, f"FLOOD_WAIT_{int(secs)}")

        st = _state.state()
        body = await _body(request)
        peer = body.get("peer") or {}
        try:
            dialog_id = int(peer.get("dialog_id"))
        except (TypeError, ValueError):
            return _err(400, "PEER_ID_INVALID")
        provided_hash = peer.get("access_hash")

        dialog = await st.pool.fetchrow(
            "SELECT id, dialog_id, dialog_kind, access_hash, title "
            "FROM app_telegram.dialogs WHERE install_pk = $1 AND dialog_id = $2",
            inst["id"], dialog_id)
        if dialog is None:
            return _err(400, "PEER_ID_INVALID")
        # User/Channel peers are addressed by (id, access_hash); a wrong hash is a
        # bad peer reference (basic Chat carries no access_hash — skip the check).
        if (dialog["dialog_kind"] in ("user", "channel")
                and dialog["access_hash"] is not None
                and provided_hash is not None
                and int(provided_hash) != int(dialog["access_hash"])):
            return _err(400, "PEER_ID_INVALID")

        offset_id = int(body.get("offset_id") or 0)
        min_id = int(body.get("min_id") or 0)
        max_id = int(body.get("max_id") or 0)
        add_offset = int(body.get("add_offset") or 0)
        limit = body.get("limit")
        limit = _MAX_PAGE if limit is None else max(1, min(_MAX_PAGE, int(limit)))

        # Candidates: older than offset_id (0=newest), strictly newer than min_id,
        # strictly older than max_id (when set). Newest-first (descending id).
        rows = await st.pool.fetch(
            """
            SELECT message_id, date_ts, edit_date_ts, text, out, from_user_id
              FROM app_telegram.messages
             WHERE dialog_pk = $1
               AND ($2 = 0 OR message_id < $2)
               AND message_id > $3
               AND ($4 = 0 OR message_id < $4)
             ORDER BY message_id DESC
            """,
            dialog["id"], offset_id, min_id, max_id)
        # add_offset skips this many from the start of the descending list (newer).
        if add_offset > 0:
            rows = rows[add_offset:]
        elif add_offset < 0:
            rows = rows  # negative add_offset (newer than offset_id) — out of scope here
        page = rows[:limit]

        messages = [
            _dto.message_dto(
                message_id=r["message_id"], dialog_id=dialog["dialog_id"],
                dialog_kind=dialog["dialog_kind"], date_ts=r["date_ts"],
                edit_date_ts=r["edit_date_ts"], text=r["text"], out=r["out"],
                from_user_id=r["from_user_id"])
            for r in page
        ]
        total = await st.pool.fetchval(
            "SELECT COUNT(*) FROM app_telegram.messages WHERE dialog_pk = $1",
            dialog["id"])
        return JSONResponse({"messages": messages, "count": int(total)})

    @app.post("/messages.getDialogs")
    async def get_dialogs(request: Request):  # noqa: ANN202
        inst, err = await _require_install(request)
        if err is not None:
            return err
        st = _state.state()
        body = await _body(request)
        limit = body.get("limit")
        limit = 200 if limit is None else max(1, int(limit))
        rows = await st.pool.fetch(
            "SELECT dialog_id, dialog_kind, access_hash, title "
            "FROM app_telegram.dialogs WHERE install_pk = $1 "
            "ORDER BY dialog_id ASC LIMIT $2",
            inst["id"], limit)
        dialogs = [
            _dto.dialog_dto(dialog_id=r["dialog_id"], dialog_kind=r["dialog_kind"],
                            access_hash=r["access_hash"], title=r["title"])
            for r in rows
        ]
        return JSONResponse({"dialogs": dialogs, "count": len(dialogs)})

    @app.post("/users.getFullUser")
    async def get_full_user(request: Request):  # noqa: ANN202
        inst, err = await _require_install(request)
        if err is not None:
            return err
        return JSONResponse({"user": _dto.self_user_dto(
            user_id=inst["self_user_id"], username=inst["self_username"],
            phone=inst["self_phone"])})

    app.add_api_websocket_route("/gateway", gateway_endpoint)

    # ---- mock-only control knobs (deterministic failure paths) ----
    @app.post("/_control/flood_wait")
    async def control_flood_wait(request: Request):  # noqa: ANN202
        body = await _body(request)
        _state.state().flood_wait_seconds = int(body.get("seconds", 30))
        return {"ok": True, "flood_wait_seconds": _state.state().flood_wait_seconds}

    @app.post("/_control/reset")
    async def control_reset():  # noqa: ANN202
        _state.state().flood_wait_seconds = None
        return {"ok": True}

    @app.get("/_health")
    async def health():  # noqa: ANN202
        return {"ok": True, "service": "telegram-mock"}

    return app


app = create_app()
