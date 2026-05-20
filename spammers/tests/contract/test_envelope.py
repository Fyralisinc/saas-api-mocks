"""Top-level response envelope + transport-level fidelity.

Real Slack Web API contract:
  - Every method returns HTTP 200 (the only status-code exception is 429 for
    rate limits). Logical failures are HTTP 200 with ``{"ok": false, "error": ...}``.
  - Content-Type is ``application/json; charset=utf-8``.
  - Success bodies always carry ``"ok": true``.
"""
from __future__ import annotations

import pytest

from spammers.tests.conftest import CH_GENERAL

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_health(client):
    r = await client.get("/_health")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "service": "slack-mock"}


async def test_success_is_200_ok_true(client, auth_header):
    r = await client.get("/api/team.info", headers=auth_header)
    assert r.status_code == 200
    assert r.json()["ok"] is True


async def test_logical_error_is_http_200(client):
    # No auth → invalid_auth. Real Slack returns HTTP 200 with ok:false.
    r = await client.post("/api/conversations.history", params={"channel": CH_GENERAL})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"] == "invalid_auth"


async def test_content_type_includes_charset(client, auth_header):
    # Real Slack: "application/json; charset=utf-8".
    r = await client.get("/api/team.info", headers=auth_header)
    assert r.headers["content-type"] == "application/json; charset=utf-8"
