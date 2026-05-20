"""team.info and auth.test response contract."""
from __future__ import annotations

import pytest

from spammers.tests.conftest import BOT_USER_ID, TEAM_DOMAIN, TEAM_ID, TEAM_NAME

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_team_info(client, auth_header):
    r = await client.get("/api/team.info", headers=auth_header)
    team = r.json()["team"]
    assert team["id"] == TEAM_ID
    assert team["name"] == TEAM_NAME
    assert team["domain"] == TEAM_DOMAIN
    assert "icon" in team


async def test_auth_test(client, auth_header):
    r = await client.get("/api/auth.test", headers=auth_header)
    body = r.json()
    assert body["ok"] is True
    assert body["team_id"] == TEAM_ID
    assert body["user_id"] == BOT_USER_ID
    assert body["url"] == f"https://{TEAM_DOMAIN}.slack.com/"
    # Real Slack: bot_id is a B-prefixed id, distinct from the bot's U… user id.
    assert body["bot_id"].startswith("B")
    assert body["bot_id"] != body["user_id"]
