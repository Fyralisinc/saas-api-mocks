"""Cursor pagination behavior — matches Slack's opaque response_metadata.next_cursor."""
from __future__ import annotations

import base64
import json

import pytest

from spammers.tests.conftest import CH_GENERAL, GENERAL_ROOT_TS_DESC

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_history_paginates(client, auth_header, reset_rate_limit):
    # Page 1 of 2 (3 roots, limit 2).
    r1 = await client.get(
        "/api/conversations.history",
        params={"channel": CH_GENERAL, "limit": 2},
        headers=auth_header,
    )
    b1 = r1.json()
    assert [m["ts"] for m in b1["messages"]] == GENERAL_ROOT_TS_DESC[:2]
    assert b1["has_more"] is True
    cursor = b1["response_metadata"]["next_cursor"]
    assert cursor

    # Cursor is an opaque base64 token (Slack-style).
    base64.urlsafe_b64decode(cursor.encode())

    reset_rate_limit()  # Tier 1: a real client would wait for the next window.
    r2 = await client.get(
        "/api/conversations.history",
        params={"channel": CH_GENERAL, "limit": 2, "cursor": cursor},
        headers=auth_header,
    )
    b2 = r2.json()
    assert [m["ts"] for m in b2["messages"]] == GENERAL_ROOT_TS_DESC[2:]
    assert b2["has_more"] is False
    assert "response_metadata" not in b2


async def test_no_cursor_when_single_page(client, auth_header):
    r = await client.get(
        "/api/conversations.history",
        params={"channel": CH_GENERAL, "limit": 15},
        headers=auth_header,
    )
    assert "response_metadata" not in r.json()
