"""conversations.{info,list,history,replies,members} response contract."""
from __future__ import annotations

import pytest

from spammers.tests.conftest import (
    CH_GENERAL,
    GENERAL_ROOT_TS_DESC,
    TS_M1,
    TS_M2,
    TS_PARENT,
    USER_ALICE,
    USER_BOB,
    USER_CAROL,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_info_shape(client, auth_header):
    r = await client.get("/api/conversations.info", params={"channel": CH_GENERAL}, headers=auth_header)
    ch = r.json()["channel"]
    assert ch["id"] == CH_GENERAL
    assert ch["name"] == "general"
    assert ch["is_general"] is True
    assert ch["is_private"] is False
    assert ch["is_channel"] is True
    assert isinstance(ch["created"], int)


async def test_info_by_name(client, auth_header):
    r = await client.get("/api/conversations.info", params={"channel": "general"}, headers=auth_header)
    assert r.json()["channel"]["id"] == CH_GENERAL


async def test_list_includes_archived_by_default(client, auth_header):
    # Real Slack: exclude_archived defaults to false (archived ARE returned),
    # AND the default `types` is public_channel ONLY (private excluded unless
    # requested).
    r = await client.get("/api/conversations.list", headers=auth_header)
    names = [c["name"] for c in r.json()["channels"]]
    assert "old-stuff" in names              # archived, but included by default
    assert "general" in names
    assert "secret-plans" not in names       # private excluded by default types
    assert names == sorted(names)


async def test_list_exclude_archived_opt_in(client, auth_header):
    r = await client.get(
        "/api/conversations.list", params={"exclude_archived": "true"}, headers=auth_header
    )
    names = [c["name"] for c in r.json()["channels"]]
    assert "old-stuff" not in names          # archived, now excluded
    assert "general" in names


async def test_list_public_only(client, auth_header):
    r = await client.get(
        "/api/conversations.list", params={"types": "public_channel"}, headers=auth_header
    )
    names = [c["name"] for c in r.json()["channels"]]
    assert "secret-plans" not in names       # private excluded


async def test_history_returns_roots_newest_first(client, auth_header):
    r = await client.get(
        "/api/conversations.history", params={"channel": CH_GENERAL}, headers=auth_header
    )
    body = r.json()
    assert body["ok"] is True
    ts_list = [m["ts"] for m in body["messages"]]
    assert ts_list == GENERAL_ROOT_TS_DESC   # parent, m2, m1 — replies excluded
    assert body["has_more"] is False


async def test_history_oldest_exclusive(client, auth_header):
    r = await client.get(
        "/api/conversations.history",
        params={"channel": CH_GENERAL, "oldest": TS_M1},
        headers=auth_header,
    )
    ts_list = [m["ts"] for m in r.json()["messages"]]
    assert TS_M1 not in ts_list
    assert ts_list == [TS_PARENT, TS_M2]


async def test_history_oldest_inclusive(client, auth_header):
    r = await client.get(
        "/api/conversations.history",
        params={"channel": CH_GENERAL, "oldest": TS_M1, "inclusive": "true"},
        headers=auth_header,
    )
    ts_list = [m["ts"] for m in r.json()["messages"]]
    assert TS_M1 in ts_list


async def test_history_latest_exclusive(client, auth_header):
    r = await client.get(
        "/api/conversations.history",
        params={"channel": CH_GENERAL, "latest": TS_M2},
        headers=auth_header,
    )
    ts_list = [m["ts"] for m in r.json()["messages"]]
    assert ts_list == [TS_M1]


async def test_members(client, auth_header):
    r = await client.get(
        "/api/conversations.members", params={"channel": CH_GENERAL}, headers=auth_header
    )
    members = r.json()["members"]
    assert set(members) == {USER_ALICE, USER_BOB, USER_CAROL}


async def test_history_unknown_channel(client, auth_header):
    r = await client.get(
        "/api/conversations.history", params={"channel": "C0NOPE0000"}, headers=auth_header
    )
    assert r.json() == {"ok": False, "error": "channel_not_found"}
