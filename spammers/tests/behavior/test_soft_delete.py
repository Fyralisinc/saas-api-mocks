"""chat.delete hides from history; chat.update stamps an `edited` block."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _post(client, auth_header, text):
    r = await client.post(
        "/api/chat.postMessage",
        headers={**auth_header, "Content-Type": "application/json"},
        json={"channel": "random", "text": text},
    )
    return r.json()["ts"]


async def _history_ts(client, auth_header, reset_rate_limit):
    reset_rate_limit()  # conversations.history is Tier 1 (~1/min); reset per read.
    r = await client.get(
        "/api/conversations.history", params={"channel": "random"}, headers=auth_header
    )
    return [m["ts"] for m in r.json()["messages"]]


async def test_delete_hides_message(client, auth_header, reset_rate_limit):
    ts = await _post(client, auth_header, "delete me")
    assert ts in await _history_ts(client, auth_header, reset_rate_limit)

    await client.post(
        "/api/chat.delete",
        headers={**auth_header, "Content-Type": "application/json"},
        json={"channel": "random", "ts": ts},
    )
    assert ts not in await _history_ts(client, auth_header, reset_rate_limit)


async def test_update_sets_edited(client, auth_header, reset_rate_limit):
    ts = await _post(client, auth_header, "original")
    await client.post(
        "/api/chat.update",
        headers={**auth_header, "Content-Type": "application/json"},
        json={"channel": "random", "ts": ts, "text": "updated"},
    )
    reset_rate_limit()
    r = await client.get(
        "/api/conversations.history", params={"channel": "random"}, headers=auth_header
    )
    msg = next(m for m in r.json()["messages"] if m["ts"] == ts)
    assert msg["text"] == "updated"
    assert "edited" in msg and "ts" in msg["edited"]
