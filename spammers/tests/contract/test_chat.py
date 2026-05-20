"""chat.postMessage / chat.update / chat.delete response contract.

Real Slack chat.postMessage success:
  {ok, channel:"C…", ts:"…", message:{type:"message", ts, text, bot_id:"B…", ...}}
The ``bot_id`` of an app-posted message is a *bot* id (prefix ``B``), distinct
from the bot's *user* id (prefix ``U``).
"""
from __future__ import annotations

import pytest

from spammers.tests.conftest import CH_RANDOM

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _post(client, auth_header, text="hello", channel="random"):
    return await client.post(
        "/api/chat.postMessage",
        headers={**auth_header, "Content-Type": "application/json"},
        json={"channel": channel, "text": text},
    )


async def test_post_message_shape(client, auth_header):
    r = await _post(client, auth_header, text="contract test message")
    body = r.json()
    assert body["ok"] is True
    assert body["channel"] == CH_RANDOM
    assert "ts" in body and "." in body["ts"]
    msg = body["message"]
    assert msg["type"] == "message"
    assert msg["text"] == "contract test message"
    assert msg["ts"] == body["ts"]


async def test_post_message_bot_id_prefix(client, auth_header):
    # Real Slack: an app/bot message carries bot_id starting with "B".
    body = (await _post(client, auth_header)).json()
    assert body["message"]["bot_id"].startswith("B")


async def test_post_message_unknown_channel(client, auth_header):
    r = await client.post(
        "/api/chat.postMessage",
        headers={**auth_header, "Content-Type": "application/json"},
        json={"channel": "C0NOTREAL0", "text": "x"},
    )
    assert r.json() == {"ok": False, "error": "channel_not_found"}


async def test_update_then_delete(client, auth_header):
    posted = (await _post(client, auth_header, text="to be edited")).json()
    ts = posted["ts"]

    upd = await client.post(
        "/api/chat.update",
        headers={**auth_header, "Content-Type": "application/json"},
        json={"channel": "random", "ts": ts, "text": "edited text"},
    )
    ub = upd.json()
    assert ub["ok"] is True
    assert ub["ts"] == ts
    assert ub["text"] == "edited text"

    dl = await client.post(
        "/api/chat.delete",
        headers={**auth_header, "Content-Type": "application/json"},
        json={"channel": "random", "ts": ts},
    )
    db = dl.json()
    assert db["ok"] is True
    assert db["ts"] == ts


async def test_update_missing_message(client, auth_header):
    r = await client.post(
        "/api/chat.update",
        headers={**auth_header, "Content-Type": "application/json"},
        json={"channel": "random", "ts": "1.2", "text": "x"},
    )
    assert r.json() == {"ok": False, "error": "message_not_found"}
