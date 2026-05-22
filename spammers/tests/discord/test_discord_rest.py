"""Discord REST surface — reads, writes, OAuth, command registration."""
from __future__ import annotations

import re

import pytest

from spammers.tests.discord.conftest import (
    APPLICATION_ID,
    BOT_TOKEN,
    CHANNEL_GENERAL,
    CLIENT_ID,
    CLIENT_SECRET,
    GENERAL_NEWEST_FIRST,
    GUILD_ID,
    MSG1,
    MSG2,
    USER_ALICE,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_unauthorized_without_token(dc_client):
    r = await dc_client.get("/api/v10/users/@me")
    assert r.status_code == 401
    body = r.json()
    assert "code" in body and "message" in body


async def test_get_me(dc_client, auth_header):
    r = await dc_client.get("/api/v10/users/@me", headers=auth_header)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == APPLICATION_ID
    assert body["bot"] is True


async def test_get_user(dc_client, auth_header):
    r = await dc_client.get(f"/api/v10/users/{USER_ALICE}", headers=auth_header)
    assert r.status_code == 200
    assert r.json()["username"] == "alice"


async def test_get_guild_and_channels(dc_client, auth_header):
    r = await dc_client.get(f"/api/v10/guilds/{GUILD_ID}", headers=auth_header)
    assert r.status_code == 200
    assert r.json()["id"] == GUILD_ID

    r = await dc_client.get(f"/api/v10/guilds/{GUILD_ID}/channels", headers=auth_header)
    assert r.status_code == 200
    names = {c["name"] for c in r.json()}
    assert {"general", "off-topic"} <= names


async def test_get_member(dc_client, auth_header):
    r = await dc_client.get(
        f"/api/v10/guilds/{GUILD_ID}/members/{USER_ALICE}", headers=auth_header
    )
    assert r.status_code == 200
    assert r.json()["user"]["id"] == USER_ALICE


async def test_messages_newest_first(dc_client, auth_header):
    r = await dc_client.get(
        f"/api/v10/channels/{CHANNEL_GENERAL}/messages", headers=auth_header
    )
    assert r.status_code == 200
    ids = [m["id"] for m in r.json()]
    assert ids == GENERAL_NEWEST_FIRST  # Discord returns newest-first


async def test_messages_before_after(dc_client, auth_header):
    base = f"/api/v10/channels/{CHANNEL_GENERAL}/messages"
    r = await dc_client.get(base, params={"before": MSG2}, headers=auth_header)
    assert [m["id"] for m in r.json()] == [MSG1]
    r = await dc_client.get(base, params={"after": MSG2}, headers=auth_header)
    assert [m["id"] for m in r.json()] == [GENERAL_NEWEST_FIRST[0]]


async def test_messages_limit(dc_client, auth_header):
    r = await dc_client.get(
        f"/api/v10/channels/{CHANNEL_GENERAL}/messages",
        params={"limit": 1}, headers=auth_header,
    )
    assert len(r.json()) == 1


async def test_create_then_read_message(dc_client, auth_header):
    r = await dc_client.post(
        f"/api/v10/channels/{CHANNEL_GENERAL}/messages",
        json={"content": "hello from the bot"}, headers=auth_header,
    )
    assert r.status_code == 200
    created = r.json()
    assert created["content"] == "hello from the bot"
    assert created["author"]["bot"] is True

    r = await dc_client.get(
        f"/api/v10/channels/{CHANNEL_GENERAL}/messages",
        params={"limit": 1}, headers=auth_header,
    )
    assert r.json()[0]["id"] == created["id"]  # newest


async def test_edit_message(dc_client, auth_header):
    r = await dc_client.patch(
        f"/api/v10/channels/{CHANNEL_GENERAL}/messages/{MSG1}",
        json={"content": "edited"}, headers=auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["content"] == "edited"
    assert body["edited_timestamp"] is not None


async def test_delete_message(dc_client, auth_header):
    r = await dc_client.delete(
        f"/api/v10/channels/{CHANNEL_GENERAL}/messages/{MSG2}", headers=auth_header
    )
    assert r.status_code == 204
    r = await dc_client.get(
        f"/api/v10/channels/{CHANNEL_GENERAL}/messages/{MSG2}", headers=auth_header
    )
    assert r.status_code == 404


async def test_unknown_channel(dc_client, auth_header):
    r = await dc_client.get("/api/v10/channels/123456789/messages", headers=auth_header)
    assert r.status_code == 404
    assert r.json()["code"] == 10003


async def test_command_registration(dc_client, auth_header):
    r = await dc_client.post(
        f"/api/v10/applications/{APPLICATION_ID}/commands",
        json={"name": "ping", "description": "Ping the bot"}, headers=auth_header,
    )
    assert r.status_code == 201
    cmd = r.json()
    assert cmd["name"] == "ping"
    assert cmd["application_id"] == APPLICATION_ID

    r = await dc_client.get(
        f"/api/v10/applications/{APPLICATION_ID}/commands", headers=auth_header
    )
    assert any(c["name"] == "ping" for c in r.json())


async def test_command_requires_name(dc_client, auth_header):
    r = await dc_client.post(
        f"/api/v10/applications/{APPLICATION_ID}/commands",
        json={"description": "no name"}, headers=auth_header,
    )
    assert r.status_code == 400


async def test_oauth_authorize_and_token(dc_client):
    r = await dc_client.get(
        "/oauth2/authorize",
        params={"client_id": CLIENT_ID, "redirect_uri": "http://consumer/cb",
                "state": "xyz", "scope": "bot", "response_type": "code"},
    )
    assert r.status_code == 200
    m = re.search(r"code=([A-Za-z0-9_\-]+)", r.text)
    assert m, r.text
    code = m.group(1)

    r = await dc_client.post(
        "/api/v10/oauth2/token",
        data={"grant_type": "authorization_code", "code": code,
              "redirect_uri": "http://consumer/cb", "client_id": CLIENT_ID,
              "client_secret": CLIENT_SECRET},
    )
    assert r.status_code == 200
    tok = r.json()
    assert tok["access_token"] == BOT_TOKEN
    assert tok["token_type"] == "Bearer"


async def test_gateway_url_endpoint(dc_client):
    r = await dc_client.get("/api/v10/gateway")
    assert r.status_code == 200
    assert r.json()["url"].startswith("ws")
