"""Hard-fail fidelity tests for the Telegram mock (MTProto method contract).

These encode the real Telethon/MTProto method semantics (verified vs
core.telegram.org + the Telethon docs) and hard-fail on divergence:

  - messages.getHistory backward offset_id paging (0=newest; id < offset_id;
    newest-first; ≤100/page; next cursor = MIN id; short page = EOF),
  - min_id EXCLUSIVE incremental floor + max_id EXCLUSIVE upper bound,
  - the TL message shape (date/edit_date EPOCH SECONDS; peer_id/from_id Peers;
    from_id NULL for channel-broadcast + self-sent; out bool),
  - edit re-observe (edit_date set, message_id unchanged),
  - getDialogs (access_hash NULL for a basic chat) + getFullUser (self user),
  - auth (missing/wrong session → 401 AUTH_KEY_UNREGISTERED), bad peer
    (→ PEER_ID_INVALID), FLOOD_WAIT (RPC error 420 + server seconds),
  - the WS updates gateway: auth ack, bad-session close, updateNewMessage /
    updateEditMessage push, and the dispatcher projecting a live message row.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from spammers.orggen.live import inject_telegram_message
from spammers.tests.discord.ws_driver import ASGIWebSocketDriver, WebSocketClosed
from spammers.tests.telegram.conftest import (
    AH_DM, AH_ENG, SELF_USER_ID, SESSION, auth_headers,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")

ENG = {"dialog_id": 1001, "dialog_kind": "channel", "access_hash": AH_ENG}


async def _history(client, peer, **body):
    body["peer"] = peer
    r = await client.post("/messages.getHistory", headers=auth_headers(), json=body)
    return r


# --------------------------------------------------------------------------- paging


async def test_get_history_backward_walk_newest_first(tg_client):
    # offset_id=0 starts at the newest; page is newest-first (descending id).
    r = await _history(tg_client, ENG, offset_id=0, limit=2)
    assert r.status_code == 200, r.text
    msgs = r.json()["messages"]
    ids = [m["id"] for m in msgs]
    assert ids == [5, 4], ids  # newest-first, ≤limit

    # Canonical backward walk: next offset_id = MIN id of the page.
    next_off = min(ids)
    r2 = await _history(tg_client, ENG, offset_id=next_off, limit=2)
    ids2 = [m["id"] for m in r2.json()["messages"]]
    assert ids2 == [3, 2], ids2          # strictly older than offset_id=4

    r3 = await _history(tg_client, ENG, offset_id=min(ids2), limit=2)
    ids3 = [m["id"] for m in r3.json()["messages"]]
    assert ids3 == [1], ids3             # short page (< limit) = EOF


async def test_get_history_min_id_exclusive_floor(tg_client):
    r = await _history(tg_client, ENG, min_id=3, limit=100)
    ids = [m["id"] for m in r.json()["messages"]]
    assert ids == [5, 4], ids            # strictly GREATER than min_id=3


async def test_get_history_max_id_exclusive_upper(tg_client):
    r = await _history(tg_client, ENG, max_id=3, limit=100)
    ids = [m["id"] for m in r.json()["messages"]]
    assert ids == [2, 1], ids            # strictly LESS than max_id=3


async def test_get_history_limit_capped_at_100(tg_client):
    # Asking for more than 100 must never return more than 100 (Telethon's cap).
    r = await _history(tg_client, ENG, offset_id=0, limit=500)
    msgs = r.json()["messages"]
    assert len(msgs) <= 100
    assert len(msgs) == 5                 # the whole (small) dialog


async def test_get_history_count_is_dialog_total(tg_client):
    r = await _history(tg_client, ENG, offset_id=0, limit=2)
    assert r.json()["count"] == 5         # messagesSlice count = total in dialog


# --------------------------------------------------------------------------- message shape


async def test_message_dto_wire_shape(tg_client):
    r = await _history(tg_client, ENG, offset_id=0, limit=100)
    by_id = {m["id"]: m for m in r.json()["messages"]}
    m5 = by_id[5]
    assert m5["_"] == "message"
    # date is EPOCH SECONDS (an int, ~1.7e9), NOT an ISO string.
    assert isinstance(m5["date"], int) and 1_500_000_000 < m5["date"] < 2_000_000_000
    assert m5["edit_date"] is None        # not edited
    assert m5["out"] is False
    assert m5["message"] == "fifth"
    assert m5["peer_id"] == {"_": "peerChannel", "channel_id": 1001}
    assert m5["from_id"] == {"_": "peerUser", "user_id": 9003}
    # An outgoing (self) message keeps out=True with a from_id in a channel.
    assert by_id[4]["out"] is True


async def test_edit_sets_edit_date_same_id(tg_client):
    r = await _history(tg_client, ENG, offset_id=0, limit=100)
    m3 = {m["id"]: m for m in r.json()["messages"]}[3]
    assert m3["id"] == 3                   # id unchanged across the edit
    assert isinstance(m3["edit_date"], int) and m3["edit_date"] > m3["date"]


async def test_no_from_id_for_channel_broadcast(tg_client):
    ann = {"dialog_id": 1002, "dialog_kind": "channel", "access_hash": None}
    r = await _history(tg_client, ann, offset_id=0, limit=100)
    for m in r.json()["messages"]:
        assert m["from_id"] is None        # channel posts carry no sender


async def test_no_from_id_for_self_sent_dm(tg_client):
    dm = {"dialog_id": 3001, "dialog_kind": "user", "access_hash": AH_DM}
    r = await _history(tg_client, dm, offset_id=0, limit=100)
    by_id = {m["id"]: m for m in r.json()["messages"]}
    assert by_id[2]["from_id"] is None and by_id[2]["out"] is True  # self-sent 1:1
    assert by_id[1]["from_id"] == {"_": "peerUser", "user_id": 3001}


# --------------------------------------------------------------------------- dialogs / me


async def test_get_dialogs(tg_client):
    r = await tg_client.post("/messages.getDialogs", headers=auth_headers(), json={})
    assert r.status_code == 200, r.text
    dialogs = {d["title"]: d for d in r.json()["dialogs"]}
    assert dialogs["eng"]["dialog_kind"] == "channel"
    assert dialogs["eng"]["access_hash"] == AH_ENG
    # A basic Chat carries NO access_hash (inputPeerChat is chat_id-only).
    assert dialogs["founders"]["dialog_kind"] == "chat"
    assert dialogs["founders"]["access_hash"] is None


async def test_get_full_user_self(tg_client):
    r = await tg_client.post("/users.getFullUser", headers=auth_headers(), json={})
    assert r.status_code == 200, r.text
    u = r.json()["user"]
    assert u["id"] == SELF_USER_ID
    assert u["username"] == "selfuser"
    assert u["is_self"] is True


# --------------------------------------------------------------------------- auth / errors


async def test_auth_missing_session_unauthorized(tg_client):
    r = await tg_client.post("/messages.getHistory", json={"peer": ENG})
    assert r.status_code == 401
    assert r.json()["error_message"] == "AUTH_KEY_UNREGISTERED"


async def test_auth_wrong_session_unauthorized(tg_client):
    r = await tg_client.post("/messages.getHistory",
                             headers={"Authorization": "Session not-the-session"},
                             json={"peer": ENG})
    assert r.status_code == 401
    assert r.json()["error_message"] == "AUTH_KEY_UNREGISTERED"


async def test_unknown_peer_invalid(tg_client):
    r = await _history(tg_client, {"dialog_id": 9999, "dialog_kind": "channel"}, limit=10)
    assert r.status_code == 400
    assert r.json()["error_message"] == "PEER_ID_INVALID"


async def test_wrong_access_hash_invalid(tg_client):
    bad = {"dialog_id": 1001, "dialog_kind": "channel", "access_hash": 1234567}
    r = await _history(tg_client, bad, limit=10)
    assert r.status_code == 400
    assert r.json()["error_message"] == "PEER_ID_INVALID"


async def test_flood_wait_one_shot(tg_client):
    armed = await tg_client.post("/_control/flood_wait", json={"seconds": 30})
    assert armed.status_code == 200
    r = await _history(tg_client, ENG, limit=10)
    assert r.status_code == 420
    assert r.json()["error_message"] == "FLOOD_WAIT_30"
    # One-shot: the next read succeeds.
    r2 = await _history(tg_client, ENG, limit=10)
    assert r2.status_code == 200


# --------------------------------------------------------------------------- live gateway


def _app():
    from spammers.telegram.app import create_app
    return create_app()


async def test_gateway_auth_ack(tg_client):
    async with ASGIWebSocketDriver(_app(), query=f"session={SESSION}") as ws:
        ack = await ws.recv_json()
        assert ack["_"] == "updates.state"
        assert ack["user_id"] == SELF_USER_ID
        assert set(ack) >= {"pts", "qts", "seq", "date"}


async def test_gateway_bad_session_closes(tg_client):
    # A wrong session: the endpoint accepts, sends an rpc_error, then closes 4401.
    with pytest.raises(WebSocketClosed) as exc:
        async with ASGIWebSocketDriver(_app(), query="session=wrong") as ws:
            err = await ws.recv_json()
            assert err["error_message"] == "AUTH_KEY_UNREGISTERED"
            await ws.recv_json()  # → the close frame raises WebSocketClosed
    assert exc.value.code == 4401


async def test_gateway_pushes_new_message(tg_client, telegram_setup, pool):
    from spammers.telegram import state as t_state
    from spammers.telegram.gateway import UpdatesDispatcher

    run_id, _ = telegram_setup
    vnow = await pool.fetchval("SELECT virtual_now FROM org.runs WHERE id=$1", run_id)
    disp = UpdatesDispatcher(pool, run_id, t_state.state().hub)
    disp._watermark = datetime(2026, 6, 1, tzinfo=timezone.utc)  # past → push allowed

    async with ASGIWebSocketDriver(_app(), query=f"session={SESSION}") as ws:
        await ws.recv_json()  # the updates.state ack
        await inject_telegram_message(pool, run_id, dialog_title="eng",
                                      text="live ping", at_virtual=vnow)
        await disp._drain_once()
        frame = await ws.recv_json()
        assert frame["_"] == "updateNewMessage"
        msg = frame["message"]
        assert msg["message"] == "live ping"
        assert msg["id"] == 6                       # max existing (5) + 1
        assert msg["peer_id"] == {"_": "peerChannel", "channel_id": 1001}
        assert frame["dialog"]["dialog_kind"] == "channel"

    # The live row was projected (is_historical FALSE) so a later backfill dedups it.
    row = await pool.fetchrow(
        "SELECT m.is_historical FROM app_telegram.messages m "
        "JOIN app_telegram.dialogs d ON d.id=m.dialog_pk "
        "JOIN app_telegram.installations i ON i.id=d.install_pk "
        "WHERE i.run_id=$1 AND d.dialog_id=1001 AND m.message_id=6", run_id)
    assert row is not None and row["is_historical"] is False


async def test_gateway_pushes_edit_message(tg_client, telegram_setup, pool):
    from spammers.telegram import state as t_state
    from spammers.telegram.gateway import UpdatesDispatcher

    run_id, _ = telegram_setup
    vnow = await pool.fetchval("SELECT virtual_now FROM org.runs WHERE id=$1", run_id)
    disp = UpdatesDispatcher(pool, run_id, t_state.state().hub)
    disp._watermark = datetime(2026, 6, 1, tzinfo=timezone.utc)

    async with ASGIWebSocketDriver(_app(), query=f"session={SESSION}") as ws:
        await ws.recv_json()
        await inject_telegram_message(pool, run_id, dialog_title="eng",
                                      text="edited body", edit=True, at_virtual=vnow)
        await disp._drain_once()
        frame = await ws.recv_json()
        assert frame["_"] == "updateEditMessage"
        msg = frame["message"]
        assert msg["id"] == 5                        # the dialog's newest, id unchanged
        assert msg["message"] == "edited body"
        assert isinstance(msg["edit_date"], int)     # fresh edit_date set


async def test_health(tg_client):
    r = await tg_client.get("/_health")
    assert r.status_code == 200 and r.json()["ok"] is True
