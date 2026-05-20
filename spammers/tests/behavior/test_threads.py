"""Threading behavior.

Real Slack:
  - conversations.history returns thread *parents* (not the replies), and the
    parent carries thread metadata (thread_ts == its own ts, reply_count).
  - conversations.replies returns the parent followed by replies, ts ascending.
"""
from __future__ import annotations

import pytest

from spammers.tests.conftest import CH_GENERAL, TS_PARENT, TS_R1, TS_R2

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_replies_returns_parent_then_replies(client, auth_header):
    r = await client.get(
        "/api/conversations.replies",
        params={"channel": CH_GENERAL, "ts": TS_PARENT},
        headers=auth_header,
    )
    ts_list = [m["ts"] for m in r.json()["messages"]]
    assert ts_list == [TS_PARENT, TS_R1, TS_R2]   # ascending, parent first


async def test_history_excludes_replies(client, auth_header):
    r = await client.get(
        "/api/conversations.history", params={"channel": CH_GENERAL}, headers=auth_header
    )
    ts_list = [m["ts"] for m in r.json()["messages"]]
    assert TS_R1 not in ts_list
    assert TS_R2 not in ts_list


async def test_thread_parent_carries_metadata_in_history(client, auth_header):
    # Real Slack shows reply_count (and thread_ts == ts) on the parent in history.
    r = await client.get(
        "/api/conversations.history", params={"channel": CH_GENERAL}, headers=auth_header
    )
    parent = next(m for m in r.json()["messages"] if m["ts"] == TS_PARENT)
    assert parent.get("reply_count") == 2
    assert parent.get("thread_ts") == TS_PARENT
