"""users.info / users.list response contract."""
from __future__ import annotations

import pytest

from spammers.tests.conftest import TEAM_ID, USER_ALICE

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_users_info_shape(client, auth_header):
    r = await client.get("/api/users.info", params={"user": USER_ALICE}, headers=auth_header)
    body = r.json()
    assert body["ok"] is True
    u = body["user"]
    assert u["id"] == USER_ALICE
    assert u["team_id"] == TEAM_ID
    assert u["name"] == "alice"            # handle
    assert u["real_name"] == "Alice Anderson"
    assert u["is_bot"] is False
    assert u["deleted"] is False
    # tz_offset must be an integer number of seconds in real Slack.
    assert isinstance(u["tz_offset"], int)
    assert u["profile"]["email"] == "alice@fidelity-test.com"
    assert u["profile"]["display_name"] == "alice"


async def test_users_info_not_found(client, auth_header):
    r = await client.get("/api/users.info", params={"user": "U0NOPE0000"}, headers=auth_header)
    assert r.json() == {"ok": False, "error": "user_not_found"}


async def test_users_list_shape(client, auth_header):
    r = await client.get("/api/users.list", headers=auth_header)
    body = r.json()
    assert body["ok"] is True
    members = body["members"]
    ids = [m["id"] for m in members]
    # Three seeded people, ordered by handle ascending.
    assert ids == sorted(ids)
    assert USER_ALICE in ids
    assert all("id" in m and "name" in m for m in members)
