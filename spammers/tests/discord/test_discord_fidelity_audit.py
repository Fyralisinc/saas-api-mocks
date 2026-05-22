"""Fidelity audit: the Discord mock must be indistinguishable from real Discord.

These assertions encode real Discord behavior (response content-type, the
``X-RateLimit-*`` header set, the 429 body shape, snowflake id format, the
documented fields on each object, and the ``{code,message}`` error body),
independent of what the mock happened to return before.
"""
from __future__ import annotations

import pytest

from spammers.tests.discord.conftest import (
    APPLICATION_ID,
    CHANNEL_GENERAL,
    GUILD_ID,
    USER_ALICE,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _is_snowflake(s) -> bool:
    return isinstance(s, str) and s.isdigit() and 15 <= len(s) <= 20


async def test_content_type_charset(dc_client, auth_header):
    r = await dc_client.get("/api/v10/users/@me", headers=auth_header)
    assert r.headers["content-type"] == "application/json; charset=utf-8"


async def test_ratelimit_headers_present(dc_client, auth_header):
    r = await dc_client.get(f"/api/v10/guilds/{GUILD_ID}", headers=auth_header)
    assert r.status_code == 200
    for h in ("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset",
              "X-RateLimit-Reset-After", "X-RateLimit-Bucket"):
        assert h in r.headers, f"missing {h}"


async def test_ratelimit_429_body_and_headers(dc_client, auth_header):
    # Hammer one route until the bucket (capacity 10) is exhausted.
    limited = None
    for _ in range(60):
        r = await dc_client.get(f"/api/v10/guilds/{GUILD_ID}", headers=auth_header)
        if r.status_code == 429:
            limited = r
            break
    assert limited is not None, "expected a 429 after exhausting the bucket"
    body = limited.json()
    assert body["message"] == "You are being rate limited."
    assert isinstance(body["retry_after"], (int, float))
    assert body["global"] is False
    assert "Retry-After" in limited.headers
    assert limited.headers["X-RateLimit-Global"] == "false"


async def test_user_object_shape(dc_client, auth_header):
    u = (await dc_client.get(f"/api/v10/users/{USER_ALICE}", headers=auth_header)).json()
    for key in ("id", "username", "discriminator", "global_name", "avatar", "bot"):
        assert key in u, f"user missing {key}"
    assert _is_snowflake(u["id"])
    assert u["bot"] is False


async def test_message_object_shape(dc_client, auth_header):
    msgs = (await dc_client.get(
        f"/api/v10/channels/{CHANNEL_GENERAL}/messages", headers=auth_header
    )).json()
    m = msgs[0]
    for key in ("id", "channel_id", "author", "content", "timestamp",
                "edited_timestamp", "tts", "mention_everyone", "mentions",
                "attachments", "embeds", "pinned", "type"):
        assert key in m, f"message missing {key}"
    assert _is_snowflake(m["id"])
    assert _is_snowflake(m["channel_id"])
    assert m["channel_id"] == CHANNEL_GENERAL
    assert m["guild_id"] == GUILD_ID
    assert "T" in m["timestamp"]  # ISO-8601
    assert isinstance(m["mentions"], list)


async def test_guild_object_shape(dc_client, auth_header):
    g = (await dc_client.get(f"/api/v10/guilds/{GUILD_ID}", headers=auth_header)).json()
    for key in ("id", "name", "owner_id", "roles", "emojis", "features",
                "verification_level", "premium_tier"):
        assert key in g, f"guild missing {key}"
    assert _is_snowflake(g["id"])
    # @everyone role shares the guild id, like real Discord.
    assert any(role["id"] == GUILD_ID and role["name"] == "@everyone" for role in g["roles"])


async def test_channel_object_shape(dc_client, auth_header):
    c = (await dc_client.get(f"/api/v10/channels/{CHANNEL_GENERAL}", headers=auth_header)).json()
    for key in ("id", "type", "name", "guild_id", "position", "permission_overwrites"):
        assert key in c, f"channel missing {key}"
    assert c["type"] == 0  # GUILD_TEXT


async def test_error_body_shape(dc_client, auth_header):
    r = await dc_client.get("/api/v10/channels/999999999999999999", headers=auth_header)
    assert r.status_code == 404
    body = r.json()
    assert set(body) >= {"code", "message"}
    assert isinstance(body["code"], int)


async def test_unauthorized_body_shape(dc_client):
    r = await dc_client.get(f"/api/v10/guilds/{GUILD_ID}")
    assert r.status_code == 401
    body = r.json()
    assert set(body) >= {"code", "message"}


async def test_bot_user_is_application_id(dc_client, auth_header):
    u = (await dc_client.get("/api/v10/users/@me", headers=auth_header)).json()
    assert u["id"] == APPLICATION_ID  # the bot user id equals the application id
    assert u["bot"] is True
