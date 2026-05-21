"""App-JWT-authed App API: /app, /app/installations, access_tokens."""
from __future__ import annotations

import pytest

from spammers.tests.github.conftest import (
    APP_ID,
    INSTALLATION_ID,
    ACCOUNT_LOGIN,
    SLUG,
    app_jwt,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_get_app(gh_client, jwt_header):
    r = await gh_client.get("/app", headers=jwt_header)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == APP_ID
    assert body["slug"] == SLUG


async def test_get_app_no_jwt_is_401(gh_client):
    r = await gh_client.get("/app")
    assert r.status_code == 401
    assert r.json()["message"] == "Bad credentials"


async def test_get_app_bad_jwt_is_401(gh_client):
    r = await gh_client.get("/app", headers={"Authorization": "Bearer not.a.jwt"})
    assert r.status_code == 401


async def test_get_app_wrong_key_is_401(gh_client):
    # A JWT signed by a different key must not verify against the app's public key.
    from spammers.common.signing import generate_rsa_keypair
    other_priv, _ = generate_rsa_keypair()
    forged = app_jwt(private_pem=other_priv)
    r = await gh_client.get("/app", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401


async def test_list_installations(gh_client, jwt_header):
    r = await gh_client.get("/app/installations", headers=jwt_header)
    assert r.status_code == 200
    insts = r.json()
    assert any(i["id"] == INSTALLATION_ID for i in insts)
    inst = insts[0]
    assert inst["account"]["login"] == ACCOUNT_LOGIN
    assert inst["app_id"] == APP_ID


async def test_get_installation(gh_client, jwt_header):
    r = await gh_client.get(f"/app/installations/{INSTALLATION_ID}", headers=jwt_header)
    assert r.status_code == 200
    assert r.json()["id"] == INSTALLATION_ID


async def test_get_unknown_installation_404(gh_client, jwt_header):
    r = await gh_client.get("/app/installations/999999", headers=jwt_header)
    assert r.status_code == 404
    assert r.json()["message"] == "Not Found"


async def test_mint_access_token(gh_client, jwt_header):
    r = await gh_client.post(
        f"/app/installations/{INSTALLATION_ID}/access_tokens", headers=jwt_header
    )
    assert r.status_code == 201           # GitHub returns 201 Created
    body = r.json()
    assert body["token"].startswith("ghs_")
    assert "expires_at" in body
    assert body["repository_selection"] == "all"
