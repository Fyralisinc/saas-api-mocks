"""Hard-fail fidelity tests for the Signal mock (signal-cli method contract).

These encode the verified SignalClient method semantics + the REAL signal-cli
envelope shapes (verified vs the signal-cli man page + Java source records) and
hard-fail on divergence:

  - get_history backward offset_ts paging (0=newest; ts < offset_ts; newest-first;
    <=100/page; next cursor = MIN ts; short page = EOF) + min_ts EXCLUSIVE floor,
  - the message id IS the timestamp in MILLISECONDS (no separate integer id),
  - the signal-cli envelope shape: inbound dataMessage (source*, groupInfo for a
    group); own/outgoing syncMessage.sentMessage (out analog, no first-class sender);
    direct vs group distinction,
  - iter_threads (direct + group) + me (the linked-account identity),
  - auth (missing/wrong session → 401 signal_api_unauthorized), unknown thread
    (→ signal_api_error), rate-limit (one-shot 429 + retry_after),
  - has_history_since (the reconciler 1-row gap probe),
  - the WS receive gateway: subscribe ack, bad-session close, a `receive`
    notification push, the own-outgoing (out=True) live skip, and the dispatcher
    projecting a live message row.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from spammers.orggen.live import inject_signal_message
from spammers.tests.discord.ws_driver import ASGIWebSocketDriver, WebSocketClosed
# NB: this test dir is `signal_source/` (not `signal/`) because a top-level
# `signal` package would shadow Python's stdlib `signal` module under pytest's
# prepend import mode. The mock module itself is `spammers.signal`.
from spammers.tests.signal_source.conftest import (
    ALICE, BOB, DM_PEER, GROUP_ENG, GROUP_FND, SELF_NUMBER, SELF_UUID, SESSION,
    auth_headers,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")

ENG = {"thread_id": GROUP_ENG}
_T0 = 1748736000000
_H = 3600000


async def _history(client, thread, **body):
    body["thread"] = thread
    return await client.post("/v1/get_history", headers=auth_headers(), json=body)


# --------------------------------------------------------------------------- paging


async def test_get_history_backward_walk_newest_first(sig_client):
    # offset_ts=0 starts at the newest; page is newest-first (descending ts).
    r = await _history(sig_client, ENG, offset_ts=0, limit=2)
    assert r.status_code == 200, r.text
    msgs = r.json()["messages"]
    ts = [m["timestamp"] for m in msgs]
    assert ts == [_T0 + 4 * _H, _T0 + 3 * _H], ts  # newest-first, <=limit

    # Canonical backward walk: next offset_ts = MIN ts of the page.
    r2 = await _history(sig_client, ENG, offset_ts=min(ts), limit=2)
    ts2 = [m["timestamp"] for m in r2.json()["messages"]]
    assert ts2 == [_T0 + 2 * _H, _T0 + 1 * _H], ts2  # strictly older than offset

    r3 = await _history(sig_client, ENG, offset_ts=min(ts2), limit=2)
    ts3 = [m["timestamp"] for m in r3.json()["messages"]]
    assert ts3 == [_T0 + 0 * _H], ts3  # short page (< limit) = EOF


async def test_get_history_min_ts_exclusive_floor(sig_client):
    r = await _history(sig_client, ENG, min_ts=_T0 + 2 * _H, limit=100)
    ts = [m["timestamp"] for m in r.json()["messages"]]
    assert ts == [_T0 + 4 * _H, _T0 + 3 * _H], ts  # strictly GREATER than min_ts


async def test_get_history_limit_capped_at_100(sig_client):
    r = await _history(sig_client, ENG, offset_ts=0, limit=500)
    msgs = r.json()["messages"]
    assert len(msgs) <= 100
    assert len(msgs) == 5  # the whole (small) thread


async def test_get_history_count_is_thread_total(sig_client):
    r = await _history(sig_client, ENG, offset_ts=0, limit=2)
    assert r.json()["count"] == 5


# --------------------------------------------------------------------------- envelope shape


async def test_inbound_data_message_shape(sig_client):
    r = await _history(sig_client, ENG, offset_ts=0, limit=100)
    by_ts = {m["timestamp"]: m for m in r.json()["messages"]}
    m5 = by_ts[_T0 + 4 * _H]
    # The message id IS the timestamp in MILLISECONDS (a ~1.7e12 integer).
    assert isinstance(m5["timestamp"], int) and m5["timestamp"] > 1_000_000_000_000
    # An inbound message is a dataMessage; the sender is in source*.
    assert "dataMessage" in m5 and "syncMessage" not in m5
    assert m5["sourceUuid"] == BOB
    assert m5["sourceNumber"] == "+15551110002"
    assert m5["dataMessage"]["message"] == "fifth"
    assert m5["dataMessage"]["timestamp"] == m5["timestamp"]
    # A group message carries groupInfo with the base64 groupId + revision.
    gi = m5["dataMessage"]["groupInfo"]
    assert gi["groupId"] == GROUP_ENG
    assert gi["groupName"] == "eng" and gi["revision"] == 3 and gi["type"] == "DELIVER"


async def test_self_sent_is_sync_message_no_sender(sig_client):
    r = await _history(sig_client, ENG, offset_ts=0, limit=100)
    by_ts = {m["timestamp"]: m for m in r.json()["messages"]}
    m3 = by_ts[_T0 + 2 * _H]  # the self-sent group message
    # Own/outgoing → syncMessage.sentMessage (NOT a dataMessage), self in source*.
    assert "syncMessage" in m3 and "dataMessage" not in m3
    assert m3["sourceUuid"] == SELF_UUID
    sent = m3["syncMessage"]["sentMessage"]
    assert sent["message"] == "third (self)"
    assert sent["timestamp"] == m3["timestamp"]
    # a group sentMessage carries groupInfo (not a destination).
    assert sent["groupInfo"]["groupId"] == GROUP_ENG


async def test_direct_thread_no_group_info(sig_client):
    dm = {"thread_id": DM_PEER}
    r = await _history(sig_client, dm, offset_ts=0, limit=100)
    by_ts = {m["timestamp"]: m for m in r.json()["messages"]}
    inbound = by_ts[_T0 + 0 * _H]
    assert inbound["sourceUuid"] == DM_PEER
    assert "groupInfo" not in inbound["dataMessage"]  # 1:1 direct: no group context
    # the self-sent direct message → syncMessage.sentMessage carries the destination.
    self_sent = by_ts[_T0 + 1 * _H]
    sent = self_sent["syncMessage"]["sentMessage"]
    assert sent["destinationUuid"] == DM_PEER and "groupInfo" not in sent


# --------------------------------------------------------------------------- threads / me


async def test_iter_threads(sig_client):
    r = await sig_client.post("/v1/iter_threads", headers=auth_headers(), json={})
    assert r.status_code == 200, r.text
    threads = {t["thread_title"]: t for t in r.json()["threads"]}
    assert threads["eng"]["thread_kind"] == "group"
    assert threads["eng"]["thread_id"] == GROUP_ENG
    assert threads["Dana DM"]["thread_kind"] == "direct"
    assert threads["Dana DM"]["thread_id"] == DM_PEER


async def test_me_self_account(sig_client):
    r = await sig_client.post("/v1/me", headers=auth_headers(), json={})
    assert r.status_code == 200, r.text
    acct = r.json()["account"]
    assert acct["number"] == SELF_NUMBER
    assert acct["uuid"] == SELF_UUID
    assert acct["username"] == "selfuser"


# --------------------------------------------------------------------------- auth / errors


async def test_auth_missing_session_unauthorized(sig_client):
    r = await sig_client.post("/v1/get_history", json={"thread": ENG})
    assert r.status_code == 401
    assert r.json()["error"]["data"]["signal_code"] == "signal_api_unauthorized"


async def test_auth_wrong_session_unauthorized(sig_client):
    r = await sig_client.post("/v1/get_history",
                              headers={"Authorization": "Session not-the-session"},
                              json={"thread": ENG})
    assert r.status_code == 401
    assert r.json()["error"]["data"]["signal_code"] == "signal_api_unauthorized"


async def test_unknown_thread_error(sig_client):
    r = await _history(sig_client, {"thread_id": "no-such-thread"}, limit=10)
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == -32602  # JSON-RPC invalid params
    assert err["data"]["signal_code"] == "signal_api_error"


async def test_rate_limit_one_shot(sig_client):
    armed = await sig_client.post("/_control/rate_limit", json={"seconds": 42})
    assert armed.status_code == 200
    r = await _history(sig_client, ENG, limit=10)
    assert r.status_code == 429
    body = r.json()
    assert body["error"]["data"]["signal_code"] == "signal_api_rate_limited"
    assert body["error"]["data"]["retry_after"] == 42
    assert r.headers.get("Retry-After") == "42"
    # One-shot: the next read succeeds.
    r2 = await _history(sig_client, ENG, limit=10)
    assert r2.status_code == 200


async def test_has_history_since(sig_client):
    # below the thread max → a gap exists.
    r = await sig_client.post("/v1/has_history_since", headers=auth_headers(),
                              json={"thread": ENG, "min_ts": _T0 + 2 * _H})
    body = r.json()
    assert body["has_more"] is True and body["newest_ts"] == _T0 + 4 * _H
    # at/above the thread max → no gap.
    r2 = await sig_client.post("/v1/has_history_since", headers=auth_headers(),
                               json={"thread": ENG, "min_ts": _T0 + 4 * _H})
    assert r2.json()["has_more"] is False and r2.json()["newest_ts"] is None


# --------------------------------------------------------------------------- live gateway


def _app():
    from spammers.signal.app import create_app
    return create_app()


async def test_gateway_subscribe_ack(sig_client):
    async with ASGIWebSocketDriver(_app(), query=f"session={SESSION}") as ws:
        ack = await ws.recv_json()
        assert ack["method"] == "subscribed"
        assert ack["params"]["account"] == SELF_NUMBER
        assert ack["params"]["uuid"] == SELF_UUID


async def test_gateway_bad_session_closes(sig_client):
    with pytest.raises(WebSocketClosed) as exc:
        async with ASGIWebSocketDriver(_app(), query="session=wrong") as ws:
            err = await ws.recv_json()
            assert err["error"]["data"]["signal_code"] == "signal_api_unauthorized"
            await ws.recv_json()  # → the close frame raises WebSocketClosed
    assert exc.value.code == 4401


async def test_gateway_pushes_receive_notification(sig_client, signal_setup, pool):
    from spammers.signal import state as s_state
    from spammers.signal.gateway import ReceiveDispatcher

    run_id, _ = signal_setup
    vnow = await pool.fetchval("SELECT virtual_now FROM org.runs WHERE id=$1", run_id)
    disp = ReceiveDispatcher(pool, run_id, s_state.state().hub)
    disp._watermark = datetime(2026, 6, 1, tzinfo=timezone.utc)  # past → push allowed

    async with ASGIWebSocketDriver(_app(), query=f"session={SESSION}") as ws:
        await ws.recv_json()  # the subscribe ack
        await inject_signal_message(pool, run_id, thread_title="eng",
                                    handle="alice", text="live ping", at_virtual=vnow)
        await disp._drain_once()
        frame = await ws.recv_json()
        assert frame["method"] == "receive"
        env = frame["params"]["envelope"]
        assert env["dataMessage"]["message"] == "live ping"
        assert env["sourceUuid"] == ALICE
        assert env["dataMessage"]["groupInfo"]["groupId"] == GROUP_ENG
        assert env["timestamp"] == int(vnow.timestamp() * 1000)

    # The live row was projected (is_historical FALSE) so a later backfill dedups it.
    row = await pool.fetchrow(
        "SELECT m.is_historical FROM app_signal.messages m "
        "JOIN app_signal.threads t ON t.id=m.thread_pk "
        "JOIN app_signal.installations i ON i.id=t.install_pk "
        "WHERE i.run_id=$1 AND t.thread_id=$2 AND m.body='live ping'", run_id, GROUP_ENG)
    assert row is not None and row["is_historical"] is False


async def test_gateway_skips_own_outgoing(sig_client, signal_setup, pool):
    from spammers.signal import state as s_state
    from spammers.signal.gateway import ReceiveDispatcher

    run_id, _ = signal_setup
    vnow = await pool.fetchval("SELECT virtual_now FROM org.runs WHERE id=$1", run_id)
    disp = ReceiveDispatcher(pool, run_id, s_state.state().hub)
    disp._watermark = datetime(2026, 6, 1, tzinfo=timezone.utc)

    async with ASGIWebSocketDriver(_app(), query=f"session={SESSION}") as ws:
        await ws.recv_json()  # the subscribe ack
        await inject_signal_message(pool, run_id, thread_title="eng",
                                    self_sent=True, text="my own message", at_virtual=vnow)
        await disp._drain_once()
        # Own outgoing (out=True) is SKIPPED on the live fan-out — no frame arrives.
        with pytest.raises(asyncio.TimeoutError):
            await ws.recv_json(timeout=1.0)

    # …but the row IS projected (is_historical FALSE) so backfill still captures it.
    row = await pool.fetchrow(
        "SELECT m.out, m.is_historical FROM app_signal.messages m "
        "JOIN app_signal.threads t ON t.id=m.thread_pk "
        "JOIN app_signal.installations i ON i.id=t.install_pk "
        "WHERE i.run_id=$1 AND t.thread_id=$2 AND m.body='my own message'",
        run_id, GROUP_ENG)
    assert row is not None and row["out"] is True and row["is_historical"] is False


async def test_health(sig_client):
    r = await sig_client.get("/_health")
    assert r.status_code == 200 and r.json()["ok"] is True
