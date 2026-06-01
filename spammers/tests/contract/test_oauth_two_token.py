"""OAuth v2 two-token issuance: a distinct xoxp user token when user_scope is
requested, gated correctly, with the right credential errors."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import pytest

from spammers.tests.conftest import (
    BOT_TOKEN,
    CLIENT_ID,
    CLIENT_SECRET,
    USER_BOB,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")

REDIRECT_URI = "https://consumer.test/callback"
USER_SCOPE = "im:read,im:history,mpim:read,mpim:history"


def _code(html: str) -> str:
    m = re.search(r"url=([^\"'>]+)", html)
    assert m
    return parse_qs(urlparse(m.group(1)).query)["code"][0]


async def _authorize(client, **extra):
    params = {"client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI, "state": "s", **extra}
    r = await client.get("/oauth/v2/authorize", params=params)
    return _code(r.text)


async def test_user_scope_mints_distinct_xoxp_token(client):
    code = await _authorize(client, user_scope=USER_SCOPE, user_id=USER_BOB)
    r = await client.post(
        "/api/oauth.v2.access",
        data={"code": code, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
    )
    body = r.json()
    assert body["ok"] is True
    # Top-level token is the bot token; authed_user carries a DISTINCT xoxp token.
    assert body["access_token"] == BOT_TOKEN
    au = body["authed_user"]
    assert au["id"] == USER_BOB
    assert au["token_type"] == "user"
    assert au["access_token"].startswith("xoxp-")
    assert au["access_token"] != BOT_TOKEN
    assert au["scope"] == USER_SCOPE
    assert body["is_enterprise_install"] is False


async def test_minted_user_token_actually_reads_dms(client):
    # End-to-end: the xoxp token returned by OAuth must work against the DM APIs.
    code = await _authorize(client, user_scope=USER_SCOPE, user_id=USER_BOB)
    r = await client.post(
        "/api/oauth.v2.access",
        data={"code": code, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
    )
    xoxp = r.json()["authed_user"]["access_token"]
    h = await client.get(
        "/api/conversations.list", params={"types": "im,mpim"},
        headers={"Authorization": f"Bearer {xoxp}"},
    )
    assert h.json()["ok"] is True
    assert len(h.json()["channels"]) >= 1


async def test_no_user_scope_omits_user_token(client):
    code = await _authorize(client)   # no user_scope
    r = await client.post(
        "/api/oauth.v2.access",
        data={"code": code, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
    )
    au = r.json()["authed_user"]
    # Real Slack: authed_user has only the installing user's id, no access_token.
    assert "access_token" not in au
    assert "token_type" not in au
    assert "id" in au


async def test_bad_client_secret_is_distinct_error(client):
    code = await _authorize(client)
    r = await client.post(
        "/api/oauth.v2.access",
        data={"code": code, "client_id": CLIENT_ID, "client_secret": "totally-wrong"},
    )
    # Valid client_id + wrong secret → bad_client_secret (NOT invalid_client_id).
    assert r.json() == {"ok": False, "error": "bad_client_secret"}


async def test_unknown_client_id_is_invalid_client_id(client):
    # Authorize with an unknown client renders the HTML error page, so drive the
    # exchange directly with a bogus client_id.
    r = await client.post(
        "/api/oauth.v2.access",
        data={"code": "whatever", "client_id": "999.999", "client_secret": "x"},
    )
    assert r.json() == {"ok": False, "error": "invalid_client_id"}
