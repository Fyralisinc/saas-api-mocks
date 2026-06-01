"""chat.* argument + scope validation, faithful to real Slack's error surface."""
from __future__ import annotations

import pytest

from spammers.tests.conftest import CH_GENERAL, CH_RANDOM

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _post(client, headers, payload):
    return await client.post(
        "/api/chat.postMessage",
        headers={**headers, "Content-Type": "application/json"},
        json=payload,
    )


async def test_empty_message_is_no_text(client, auth_header):
    r = await _post(client, auth_header, {"channel": "random"})  # no text/blocks/attachments
    assert r.json() == {"ok": False, "error": "no_text"}


async def test_post_without_chat_write_scope_is_missing_scope(client, user_auth_header):
    # alice's xoxp token has im/mpim/users:read scopes but NOT chat:write.
    r = await _post(client, user_auth_header, {"channel": "general", "text": "hi"})
    body = r.json()
    assert body["ok"] is False
    assert body["error"] == "missing_scope"
    assert body["needed"] == "chat:write"


async def test_blocks_echoed_back_in_message(client, auth_header):
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "hello"}}]
    r = await _post(client, auth_header, {"channel": CH_RANDOM, "blocks": blocks, "text": ""})
    msg = r.json()["message"]
    assert msg["blocks"] == blocks
    assert msg["bot_id"].startswith("B")
    assert msg["app_id"].startswith("A")


async def test_update_bad_channel_is_channel_not_found(client, auth_header):
    r = await client.post(
        "/api/chat.update",
        headers={**auth_header, "Content-Type": "application/json"},
        json={"channel": "C0NOPE0000", "ts": "1.0", "text": "x"},
    )
    assert r.json() == {"ok": False, "error": "channel_not_found"}


async def test_update_unknown_ts_is_message_not_found(client, auth_header):
    r = await client.post(
        "/api/chat.update",
        headers={**auth_header, "Content-Type": "application/json"},
        json={"channel": CH_GENERAL, "ts": "1.000001", "text": "x"},
    )
    assert r.json() == {"ok": False, "error": "message_not_found"}
