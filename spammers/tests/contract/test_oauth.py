"""OAuth v2 contract: authorize redirect + token exchange.

Real Slack flow: authorize 302s back to redirect_uri?code=&state=, then
oauth.v2.access exchanges the code for a bot token.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import pytest

from spammers.tests.conftest import (
    APP_ID,
    BOT_TOKEN,
    BOT_USER_ID,
    CLIENT_ID,
    CLIENT_SECRET,
    TEAM_ID,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")

REDIRECT_URI = "https://consumer.test/callback"


def _extract_code(html: str) -> str:
    m = re.search(r"url=([^\"'>]+)", html)
    assert m, f"no redirect url in authorize HTML: {html[:200]}"
    qs = parse_qs(urlparse(m.group(1)).query)
    return qs["code"][0]


async def test_full_oauth_exchange(client):
    auth = await client.get(
        "/oauth/v2/authorize",
        params={"client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI, "state": "st-123"},
    )
    assert auth.status_code == 200
    assert "state=st-123" in auth.text
    code = _extract_code(auth.text)

    token = await client.post(
        "/api/oauth.v2.access",
        data={"code": code, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
    )
    body = token.json()
    assert body["ok"] is True
    assert body["access_token"] == BOT_TOKEN
    assert body["token_type"] == "bot"
    assert body["bot_user_id"] == BOT_USER_ID
    assert body["app_id"] == APP_ID
    assert body["team"]["id"] == TEAM_ID
    assert "chat:write" in body["scope"]


async def test_access_bad_secret(client):
    auth = await client.get(
        "/oauth/v2/authorize",
        params={"client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI, "state": "s"},
    )
    code = _extract_code(auth.text)
    token = await client.post(
        "/api/oauth.v2.access",
        data={"code": code, "client_id": CLIENT_ID, "client_secret": "wrong"},
    )
    # Real Slack returns HTTP 200 with ok:false for credential errors.
    assert token.status_code == 200
    assert token.json()["ok"] is False


async def test_authorize_bad_client_id_is_not_http_400(client):
    # Real Slack's authorize endpoint never returns a bare JSON 400 for a bad
    # client_id; it renders a user-facing page (HTTP 200). The mock returns 400.
    r = await client.get(
        "/oauth/v2/authorize",
        params={"client_id": "999.999", "redirect_uri": REDIRECT_URI, "state": "s"},
    )
    assert r.status_code == 200
