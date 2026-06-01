"""The two-token model — the core of the new Fyralis ingestion architecture.

A **bot token** (xoxb) reads channels and CANNOT see human DMs. A **user token**
(xoxp) reads the consenting human's 1:1 DMs (im) and group DMs (mpim). These
tests prove the mock enforces that split exactly the way real Slack does — so a
bot-token DM read that "works" against the mock can never give a false green.
"""
from __future__ import annotations

import pytest

from spammers.tests.conftest import (
    ALICE_USER_TOKEN,
    CH_DM_AB,
    CH_MPIM_ABC,
    TS_DM1,
    TS_DM2,
    USER_ALICE,
    USER_BOB,
    USER_CAROL,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


# --- user token (xoxp) sees the consenting user's DMs ----------------------

async def test_user_token_lists_im_and_mpim(client, user_auth_header):
    r = await client.get(
        "/api/conversations.list",
        params={"types": "im,mpim"},
        headers=user_auth_header,
    )
    body = r.json()
    assert body["ok"] is True
    ids = {c["id"] for c in body["channels"]}
    assert CH_DM_AB in ids       # alice's 1:1 with bob
    assert CH_MPIM_ABC in ids    # group DM alice is in


async def test_im_object_shape(client, user_auth_header):
    r = await client.get(
        "/api/conversations.list", params={"types": "im"}, headers=user_auth_header
    )
    im = next(c for c in r.json()["channels"] if c["id"] == CH_DM_AB)
    # Real im object: minimal, carries the counterpart `user`, no name/topic/purpose.
    assert im["is_im"] is True
    assert im["user"] == USER_BOB          # the OTHER human, relative to alice
    assert "name" not in im
    assert "topic" not in im
    assert "purpose" not in im
    assert im["is_org_shared"] is False


async def test_mpim_object_shape(client, user_auth_header):
    r = await client.get(
        "/api/conversations.list", params={"types": "mpim"}, headers=user_auth_header
    )
    mp = next(c for c in r.json()["channels"] if c["id"] == CH_MPIM_ABC)
    assert mp["is_mpim"] is True
    assert mp["is_im"] is False
    assert mp["name"].startswith("mpdm-")  # real Slack synthesises an mpdm-… name


async def test_user_token_reads_dm_history(client, user_auth_header):
    r = await client.get(
        "/api/conversations.history", params={"channel": CH_DM_AB}, headers=user_auth_header
    )
    body = r.json()
    assert body["ok"] is True
    seen = {m["ts"] for m in body["messages"]}
    assert {TS_DM1, TS_DM2} <= seen        # both seeded DM messages are visible


async def test_user_token_members_are_the_two_participants(client, user_auth_header):
    r = await client.get(
        "/api/conversations.members", params={"channel": CH_DM_AB}, headers=user_auth_header
    )
    assert set(r.json()["members"]) == {USER_ALICE, USER_BOB}


# --- bot token (xoxb) must NOT be able to read human DMs --------------------

async def test_bot_token_cannot_list_dms(client, auth_header):
    r = await client.get(
        "/api/conversations.list", params={"types": "im,mpim"}, headers=auth_header
    )
    body = r.json()
    assert body["ok"] is True
    # A bot token sees NO human DMs — they never appear in its listing.
    assert body["channels"] == []


async def test_bot_token_cannot_read_dm_history(client, auth_header):
    # Real Slack hides the DM from the bot entirely → channel_not_found.
    r = await client.get(
        "/api/conversations.history", params={"channel": CH_DM_AB}, headers=auth_header
    )
    assert r.json() == {"ok": False, "error": "channel_not_found"}


async def test_bot_token_cannot_read_mpim_history(client, auth_header):
    r = await client.get(
        "/api/conversations.history", params={"channel": CH_MPIM_ABC}, headers=auth_header
    )
    assert r.json() == {"ok": False, "error": "channel_not_found"}


# --- auth.test reflects the token TYPE -------------------------------------

async def test_auth_test_user_token_is_the_human(client, user_auth_header):
    r = await client.get("/api/auth.test", headers=user_auth_header)
    body = r.json()
    assert body["ok"] is True
    assert body["user_id"] == USER_ALICE   # the human, not the bot
    assert "bot_id" not in body            # only bot tokens carry bot_id


async def test_auth_test_bot_token_has_bot_id(client, auth_header):
    r = await client.get("/api/auth.test", headers=auth_header)
    body = r.json()
    assert body["ok"] is True
    assert body["bot_id"].startswith("B")
    assert body["user_id"].startswith("U")
