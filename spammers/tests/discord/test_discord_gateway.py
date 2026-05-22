"""Gateway WebSocket fidelity — handshake, heartbeats, RESUME, dispatch, intents.

These assertions encode real Discord Gateway (v10) behavior and hard-fail on
divergence: HELLO/IDENTIFY/READY ordering, sequence numbering, close codes,
RESUME replay, the no-historical-replay rule, and privileged-intent gating.
"""
from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from spammers.discord.gateway import connection as conn_mod
from spammers.discord.gateway.opcodes import CloseCode, Intents, Op
from spammers.tests.discord.conftest import BOT_TOKEN, GUILD_ID
from spammers.tests.discord.ws_driver import ASGIWebSocketDriver, WebSocketClosed

pytestmark = pytest.mark.asyncio(loop_scope="session")

GUILD_MSGS = Intents.GUILD_MESSAGES
GUILD_MSGS_CONTENT = Intents.GUILD_MESSAGES | Intents.MESSAGE_CONTENT


def _identify(token: str = BOT_TOKEN, intents: int = GUILD_MSGS_CONTENT) -> dict:
    return {"op": Op.IDENTIFY.value, "d": {"token": token, "intents": intents, "properties": {}}}


async def _handshake(ws, intents: int = GUILD_MSGS_CONTENT):
    """Drive HELLO → IDENTIFY → READY → GUILD_CREATE(s). Returns the READY frame."""
    hello = await ws.recv_json()
    assert hello["op"] == Op.HELLO.value
    assert hello["d"]["heartbeat_interval"] > 0
    assert hello["s"] is None and hello["t"] is None
    await ws.send_json(_identify(intents=intents))
    ready = await ws.recv_json()
    assert ready["op"] == Op.DISPATCH.value
    assert ready["t"] == "READY"
    assert ready["s"] == 1
    # one GUILD_CREATE per guild
    gc = await ws.recv_json()
    assert gc["t"] == "GUILD_CREATE"
    assert gc["s"] == 2
    assert gc["d"]["id"] == GUILD_ID
    return ready


async def test_hello_identify_ready(gateway_app):
    async with ASGIWebSocketDriver(gateway_app) as ws:
        ready = await _handshake(ws)
        assert ready["d"]["user"]["bot"] is True
        assert ready["d"]["session_id"]
        assert ready["d"]["resume_gateway_url"].startswith("ws")
        # READY lists the guild as unavailable; the full object came via GUILD_CREATE.
        assert ready["d"]["guilds"][0]["unavailable"] is True


async def test_heartbeat_ack(gateway_app):
    async with ASGIWebSocketDriver(gateway_app) as ws:
        await _handshake(ws)
        await ws.send_json({"op": Op.HEARTBEAT.value, "d": None})
        ack = await ws.recv_json()
        assert ack["op"] == Op.HEARTBEAT_ACK.value
        assert ack["s"] is None  # control op carries no sequence


async def test_bad_token_closes_4004(gateway_app):
    async with ASGIWebSocketDriver(gateway_app) as ws:
        hello = await ws.recv_json()
        assert hello["op"] == Op.HELLO.value
        await ws.send_json(_identify(token="not-a-real-token"))
        assert await ws.expect_closed() == CloseCode.AUTHENTICATION_FAILED.value


async def test_op_before_identify_closes_4003(gateway_app):
    async with ASGIWebSocketDriver(gateway_app) as ws:
        await ws.recv_json()  # HELLO
        await ws.send_json({"op": Op.PRESENCE_UPDATE.value, "d": {}})
        assert await ws.expect_closed() == CloseCode.NOT_AUTHENTICATED.value


async def test_invalid_intents_closes_4013(gateway_app):
    async with ASGIWebSocketDriver(gateway_app) as ws:
        await ws.recv_json()  # HELLO
        await ws.send_json(_identify(intents=1 << 40))  # bit beyond ALL_KNOWN
        assert await ws.expect_closed() == CloseCode.INVALID_INTENTS.value


async def test_unknown_opcode_closes_4001(gateway_app):
    async with ASGIWebSocketDriver(gateway_app) as ws:
        await _handshake(ws)
        await ws.send_json({"op": 99, "d": None})
        assert await ws.expect_closed() == CloseCode.UNKNOWN_OPCODE.value


async def test_decode_error_closes_4002(gateway_app):
    async with ASGIWebSocketDriver(gateway_app) as ws:
        await _handshake(ws)
        # send malformed (non-JSON) text directly through the receive channel
        await ws._to_app.put({"type": "websocket.receive", "text": "{not json"})
        assert await ws.expect_closed() == CloseCode.DECODE_ERROR.value


async def test_presence_and_request_members_keep_open(gateway_app):
    async with ASGIWebSocketDriver(gateway_app) as ws:
        await _handshake(ws)
        # opcodes 3/4/8 are accepted as no-ops — the connection must stay open.
        await ws.send_json({"op": Op.PRESENCE_UPDATE.value, "d": {}})
        await ws.send_json({"op": Op.REQUEST_GUILD_MEMBERS.value, "d": {"guild_id": GUILD_ID}})
        await ws.send_json({"op": Op.HEARTBEAT.value, "d": None})
        ack = await ws.recv_json()
        assert ack["op"] == Op.HEARTBEAT_ACK.value


# --------------------------------------------------------------------------- #
# Live dispatch + no-replay watermark
# --------------------------------------------------------------------------- #

async def _inject_message(pool, run_id, *, virtual_offset_s: int, channel="general", text="live!",
                          advance_clock: bool = True):
    """Insert a not-historical discord.message at ``virtual_now + offset``.

    When ``advance_clock`` (the default for live events), jump the frozen clock
    just past the event so it becomes claimable by the dispatcher. A negative
    offset with ``advance_clock=False`` simulates a pre-watermark (historical)
    event that must NOT be dispatched.
    """
    import json
    from spammers.common.clock import get_clock, jump_to
    clock = await get_clock(pool, run_id)
    when = clock.virtual_now + timedelta(seconds=virtual_offset_s)
    event_id = uuid4()
    actor = await pool.fetchrow(
        "SELECT id FROM org.people WHERE run_id = $1 AND handle = 'alice'", run_id,
    )
    await pool.execute(
        """
        INSERT INTO timeline.events
            (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical)
        VALUES ($1, $2, $3, 'discord.message', $4, $5::jsonb, '{}'::jsonb, FALSE)
        """,
        event_id, run_id, when, actor["id"],
        json.dumps({"channel": channel, "text": text, "kind": "live"}),
    )
    if advance_clock:
        await jump_to(pool, run_id, when + timedelta(seconds=1))
    return event_id


async def test_live_message_dispatched(gateway_app, dispatcher, dc_state):
    async with ASGIWebSocketDriver(gateway_app) as ws:
        await _handshake(ws, intents=GUILD_MSGS_CONTENT)
        # establish the no-replay watermark at the current clock
        await dispatcher._drain_once()
        # a NEW event after the watermark must be delivered
        await _inject_message(dc_state.pool, dc_state.run_id, virtual_offset_s=10, text="hi there")
        await dispatcher._drain_once()
        frame = await ws.recv_json()
        assert frame["t"] == "MESSAGE_CREATE"
        assert frame["d"]["content"] == "hi there"
        assert frame["d"]["guild_id"] == GUILD_ID
        assert frame["d"]["author"]["username"] == "alice"


async def test_no_historical_replay(gateway_app, dispatcher, dc_state):
    # An event that is at/just-before the watermark must NOT be dispatched: a bot
    # connecting after the clock advanced does not receive a flood of history.
    await _inject_message(dc_state.pool, dc_state.run_id, virtual_offset_s=-5, text="old",
                          advance_clock=False)
    async with ASGIWebSocketDriver(gateway_app) as ws:
        await _handshake(ws)
        await dispatcher._drain_once()  # sets watermark; projects but does not dispatch
        with pytest.raises((WebSocketClosed, TimeoutError, Exception)):
            await ws.recv_json(timeout=0.4)


async def test_intent_gating_no_guild_messages(gateway_app, dispatcher, dc_state):
    # Without GUILD_MESSAGES the bot receives no MESSAGE_CREATE at all.
    async with ASGIWebSocketDriver(gateway_app) as ws:
        await _handshake(ws, intents=Intents.GUILDS)  # no GUILD_MESSAGES
        await dispatcher._drain_once()
        await _inject_message(dc_state.pool, dc_state.run_id, virtual_offset_s=10, text="nope")
        await dispatcher._drain_once()
        with pytest.raises((WebSocketClosed, TimeoutError, Exception)):
            await ws.recv_json(timeout=0.4)


async def test_intent_gating_no_message_content(gateway_app, dispatcher, dc_state):
    # GUILD_MESSAGES without MESSAGE_CONTENT → delivered, but content stripped.
    async with ASGIWebSocketDriver(gateway_app) as ws:
        await _handshake(ws, intents=GUILD_MSGS)  # no MESSAGE_CONTENT
        await dispatcher._drain_once()
        await _inject_message(dc_state.pool, dc_state.run_id, virtual_offset_s=10, text="secret")
        await dispatcher._drain_once()
        frame = await ws.recv_json()
        assert frame["t"] == "MESSAGE_CREATE"
        assert frame["d"]["content"] == ""  # privileged intent not granted


# --------------------------------------------------------------------------- #
# RESUME
# --------------------------------------------------------------------------- #

async def test_resume_replays_missed(gateway_app, dispatcher, dc_state):
    # Connect, take a couple dispatches, disconnect, then RESUME and assert the
    # missed dispatches are replayed with their original sequence numbers.
    async with ASGIWebSocketDriver(gateway_app) as ws:
        ready = await _handshake(ws, intents=GUILD_MSGS_CONTENT)
        session_id = ready["d"]["session_id"]
        await dispatcher._drain_once()  # watermark
        await _inject_message(dc_state.pool, dc_state.run_id, virtual_offset_s=10, text="one")
        await dispatcher._drain_once()
        first = await ws.recv_json()
        assert first["t"] == "MESSAGE_CREATE"
        last_seq = first["s"]
    # ws closed; session parked. Inject another while disconnected.
    await _inject_message(dc_state.pool, dc_state.run_id, virtual_offset_s=20, text="two")
    await dispatcher._drain_once()

    async with ASGIWebSocketDriver(gateway_app) as ws2:
        hello = await ws2.recv_json()
        assert hello["op"] == Op.HELLO.value
        await ws2.send_json({
            "op": Op.RESUME.value,
            "d": {"token": BOT_TOKEN, "session_id": session_id, "seq": last_seq},
        })
        replayed = await ws2.recv_json()
        assert replayed["t"] == "MESSAGE_CREATE"
        assert replayed["d"]["content"] == "two"
        assert replayed["s"] == last_seq + 1  # original seq preserved
        resumed = await ws2.recv_json()
        assert resumed["t"] == "RESUMED"


async def test_resume_unknown_session_invalidates(gateway_app, dc_state):
    async with ASGIWebSocketDriver(gateway_app) as ws:
        hello = await ws.recv_json()
        assert hello["op"] == Op.HELLO.value
        await ws.send_json({
            "op": Op.RESUME.value,
            "d": {"token": BOT_TOKEN, "session_id": "deadbeef", "seq": 5},
        })
        frame = await ws.recv_json()
        assert frame["op"] == Op.INVALID_SESSION.value
        assert frame["d"] is False  # non-resumable → client re-identifies


async def test_heartbeat_timeout_closes_4009(gateway_app, monkeypatch):
    # Shrink the heartbeat interval so a silent client trips the monitor fast.
    monkeypatch.setattr(conn_mod, "heartbeat_interval_ms", 50)
    async with ASGIWebSocketDriver(gateway_app) as ws:
        await _handshake(ws)
        # send no heartbeats; the monitor should close 4009 within ~1.5×50ms×…
        assert await ws.expect_closed(timeout=2.0) == CloseCode.SESSION_TIMED_OUT.value
