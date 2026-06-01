"""Channel membership errors + thread-parent roll-up fields."""
from __future__ import annotations

import pytest

from spammers.tests.conftest import (
    CH_GENERAL,
    CH_LOCKED,
    TS_PARENT,
    TS_R2,
    USER_BOB,
    USER_CAROL,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_unjoined_channel_is_not_in_channel(client, auth_header):
    # The bot is not a member of #locked-room → real Slack returns not_in_channel.
    r = await client.get(
        "/api/conversations.history", params={"channel": CH_LOCKED}, headers=auth_header
    )
    assert r.json() == {"ok": False, "error": "not_in_channel"}


async def test_unjoined_channel_lists_with_is_member_false(client, auth_header):
    r = await client.get(
        "/api/conversations.list", params={"types": "public_channel"}, headers=auth_header
    )
    locked = next(c for c in r.json()["channels"] if c["id"] == CH_LOCKED)
    assert locked["is_member"] is False


async def test_thread_parent_rollup_fields(client, auth_header):
    r = await client.get(
        "/api/conversations.history", params={"channel": CH_GENERAL}, headers=auth_header
    )
    parent = next(m for m in r.json()["messages"] if m["ts"] == TS_PARENT)
    assert parent["thread_ts"] == TS_PARENT          # parent's thread_ts == its own ts
    assert parent["reply_count"] == 2
    assert parent["reply_users_count"] == 2
    assert parent["latest_reply"] == TS_R2
    assert set(parent["reply_users"]) == {USER_BOB, USER_CAROL}


async def test_replies_returns_parent_then_replies(client, auth_header):
    r = await client.get(
        "/api/conversations.replies",
        params={"channel": CH_GENERAL, "ts": TS_PARENT},
        headers=auth_header,
    )
    msgs = r.json()["messages"]
    assert msgs[0]["ts"] == TS_PARENT                # parent first
    assert len(msgs) == 3                            # parent + 2 replies


async def test_replies_unknown_ts_is_thread_not_found(client, auth_header):
    r = await client.get(
        "/api/conversations.replies",
        params={"channel": CH_GENERAL, "ts": "1.000009"},
        headers=auth_header,
    )
    assert r.json() == {"ok": False, "error": "thread_not_found"}
