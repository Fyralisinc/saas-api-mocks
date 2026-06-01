"""Bearer-token auth contract.

Real Slack distinguishes the two failure modes: a request with NO token at all
returns ``not_authed`` ("No authentication token provided"); a request whose
token is present but invalid returns ``invalid_auth``.
"""
from __future__ import annotations

import pytest

from spammers.tests.conftest import BOT_TOKEN

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_missing_token(client):
    r = await client.get("/api/team.info")
    assert r.json() == {"ok": False, "error": "not_authed"}


async def test_bad_token(client):
    r = await client.get("/api/team.info", headers={"Authorization": "Bearer xoxb-not-real"})
    assert r.json()["error"] == "invalid_auth"


async def test_valid_token(client):
    r = await client.get("/api/team.info", headers={"Authorization": f"Bearer {BOT_TOKEN}"})
    assert r.json()["ok"] is True


async def test_bearer_prefix_case_insensitive(client):
    r = await client.get("/api/team.info", headers={"Authorization": f"bearer {BOT_TOKEN}"})
    assert r.json()["ok"] is True
