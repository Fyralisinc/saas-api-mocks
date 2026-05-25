"""Gmail mock — contract + behavior fidelity, incl. the Pub/Sub OIDC push.

Covers DWD token exchange, messages list/get (all formats), threads, history
drain, watch/stop, profile, Admin Directory enumeration, 429 quota, and the
key live-path guarantee: a signed push JWT verifies against /jwks.
"""
from __future__ import annotations

import base64
import json

import jwt
import pytest

from spammers.gmail.push import build_envelope, sign_oidc
from spammers.tests.gmail.conftest import ALICE, AUDIENCE, DOMAIN, PUSH_SA, SCOPE, gmail_token

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ---- auth -----------------------------------------------------------------

async def test_token_exchange(gmail_client):
    assertion = jwt.encode({"iss": "sa", "sub": ALICE, "scope": SCOPE}, "k" * 32, algorithm="HS256")
    r = await gmail_client.post("/token", data={
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
    assert r.status_code == 200 and r.json()["access_token"].startswith("ya29.")


async def test_unauthed_401(gmail_client):
    r = await gmail_client.get("/gmail/v1/users/me/profile")
    assert r.status_code == 401 and r.json()["error"]["status"] == "UNAUTHENTICATED"


# ---- profile / messages ---------------------------------------------------

async def test_profile(gmail_client, gmail_auth):
    r = await gmail_client.get("/gmail/v1/users/me/profile", headers=gmail_auth)
    body = r.json()
    assert body["emailAddress"] == ALICE
    assert body["messagesTotal"] == 2 and body["historyId"] == "2"


async def test_messages_list_and_pagination(gmail_client, gmail_auth):
    r = await gmail_client.get("/gmail/v1/users/me/messages", headers=gmail_auth)
    body = r.json()
    assert body["resultSizeEstimate"] == 2 and len(body["messages"]) == 2
    assert set(body["messages"][0]) == {"id", "threadId"}

    r = await gmail_client.get("/gmail/v1/users/me/messages?maxResults=1", headers=gmail_auth)
    body = r.json()
    assert len(body["messages"]) == 1 and "nextPageToken" in body


async def test_messages_get_formats(gmail_client, gmail_auth):
    mid = (await gmail_client.get("/gmail/v1/users/me/messages",
                                  headers=gmail_auth)).json()["messages"][0]["id"]
    full = (await gmail_client.get(f"/gmail/v1/users/me/messages/{mid}?format=full",
                                   headers=gmail_auth)).json()
    assert full["id"] == mid and "labelIds" in full and full["internalDate"].isdigit()
    names = {h["name"] for h in full["payload"]["headers"]}
    assert {"From", "To", "Subject", "Date", "Message-ID"} <= names
    decoded = base64.urlsafe_b64decode(full["payload"]["body"]["data"]).decode()
    assert decoded  # body present at format=full

    meta = (await gmail_client.get(f"/gmail/v1/users/me/messages/{mid}?format=metadata",
                                   headers=gmail_auth)).json()
    assert "data" not in meta["payload"]["body"]  # no body at metadata

    minimal = (await gmail_client.get(f"/gmail/v1/users/me/messages/{mid}?format=minimal",
                                      headers=gmail_auth)).json()
    assert "payload" not in minimal


async def test_message_404(gmail_client, gmail_auth):
    r = await gmail_client.get("/gmail/v1/users/me/messages/nope", headers=gmail_auth)
    assert r.status_code == 404 and r.json()["error"]["status"] == "NOT_FOUND"


# ---- threads / history ----------------------------------------------------

async def test_thread_get(gmail_client, gmail_auth):
    r = await gmail_client.get("/gmail/v1/users/me/threads/t100", headers=gmail_auth)
    body = r.json()
    assert body["id"] == "t100" and len(body["messages"]) == 2


async def test_history_drain(gmail_client, gmail_auth):
    r = await gmail_client.get("/gmail/v1/users/me/history?startHistoryId=1", headers=gmail_auth)
    body = r.json()
    assert body["historyId"] == "2"
    assert len(body["history"]) == 1  # only history_id 2 is > 1
    assert "messagesAdded" in body["history"][0]


async def test_history_requires_start(gmail_client, gmail_auth):
    r = await gmail_client.get("/gmail/v1/users/me/history", headers=gmail_auth)
    assert r.status_code == 400


# ---- watch / stop ---------------------------------------------------------

async def test_watch_and_stop(gmail_client, gmail_auth):
    r = await gmail_client.post("/gmail/v1/users/me/watch", headers=gmail_auth,
                                json={"topicName": "projects/p/topics/gmail", "labelIds": ["INBOX"]})
    body = r.json()
    assert body["historyId"] == "2" and body["expiration"].isdigit()
    r = await gmail_client.post("/gmail/v1/users/me/stop", headers=gmail_auth)
    assert r.status_code == 204


# ---- directory ------------------------------------------------------------

async def test_directory_users(gmail_client, gmail_auth):
    r = await gmail_client.get("/admin/directory/v1/users?maxResults=100", headers=gmail_auth)
    body = r.json()
    assert body["kind"] == "admin#directory#users" and len(body["users"]) == 2
    u = body["users"][0]
    assert u["primaryEmail"].endswith(f"@{DOMAIN}") and "fullName" in u["name"]


# ---- OIDC push fidelity ---------------------------------------------------

async def test_jwks_and_push_verifies(gmail_client, gmail_auth):
    certs = (await gmail_client.get("/jwks")).json()
    assert len(certs["keys"]) == 1 and certs["keys"][0]["alg"] == "RS256"
    # build a JWK public key and verify a freshly-signed push token against it
    from spammers.gmail.state import state
    st = state()
    signed = sign_oidc(st.oidc_private_key, st.oidc_public_key,
                       audience=AUDIENCE, push_sa_email=PUSH_SA)
    pub = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(certs["keys"][0]))
    decoded = jwt.decode(signed, pub, algorithms=["RS256"], audience=AUDIENCE)
    assert decoded["iss"] == "https://accounts.google.com"
    assert decoded["email"] == PUSH_SA and decoded["email_verified"] is True
    env = build_envelope(ALICE, 42, "projects/p/subscriptions/s")
    data = json.loads(base64.b64decode(env["message"]["data"]))
    assert data == {"emailAddress": ALICE, "historyId": 42}


# ---- rate limiting --------------------------------------------------------

async def test_rate_limit_429(gmail_client, gmail_auth):
    # Gmail's real ceiling (250 units/s/user) is too high to deplete with
    # sequential in-process calls, so drain the bucket deterministically, then
    # assert the next request is throttled.
    from spammers.common.rate_limit import gmail_quota
    from spammers.gmail.ratelimit import _RL
    from spammers.tests.gmail.conftest import ALICE
    cap, refill = gmail_quota()
    await _RL.take(f"gmail:{ALICE}", capacity=cap, refill_per_sec=refill, cost=cap)
    r = await gmail_client.get("/gmail/v1/users/me/messages", headers=gmail_auth)
    assert r.status_code == 429
    assert r.json()["error"]["errors"][0]["reason"] == "rateLimitExceeded"
    assert int(r.headers["Retry-After"]) >= 1
