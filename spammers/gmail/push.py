"""Gmail Pub/Sub push: OIDC signing + JWKS + envelope builder.

Real Gmail publishes to a Pub/Sub topic, which pushes an envelope to the
consumer's webhook with an ``Authorization: Bearer <OIDC JWT>``. The consumer
strictly verifies that JWT (RS256 against Google's JWKS, ``iss`` ∈
{accounts.google.com, https://accounts.google.com}, ``aud`` == configured
audience, ``email`` == configured push SA, ``email_verified`` == true, exp/iat).

The mock holds the customer's RSA keypair: it publishes the public half at
``GET /jwks`` (point the consumer's ``GOOGLE_OIDC_JWKS_URL`` there) and signs the
push JWT with the private half — so verification passes end-to-end.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from datetime import datetime, timezone

import jwt as _jwt


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _int_to_b64u(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return _b64u(raw)


def kid_for(public_pem: str) -> str:
    return hashlib.sha256(public_pem.encode()).hexdigest()[:16]


def jwk_from_public_pem(public_pem: str) -> dict:
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    pub = load_pem_public_key(public_pem.encode())
    nums = pub.public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid_for(public_pem),
        "n": _int_to_b64u(nums.n),
        "e": _int_to_b64u(nums.e),
    }


def jwks(public_pem: str) -> dict:
    return {"keys": [jwk_from_public_pem(public_pem)]}


def sign_oidc(private_pem: str, public_pem: str, *, audience: str, push_sa_email: str,
              ttl: int = 3600) -> str:
    """Sign the OIDC JWT Google would put on a Pub/Sub push."""
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": audience,
        "azp": push_sa_email,
        "email": push_sa_email,
        "email_verified": True,
        "sub": hashlib.sha256(push_sa_email.encode()).hexdigest()[:21],
        "iat": now,
        "exp": now + ttl,
    }
    return _jwt.encode(claims, private_pem, algorithm="RS256",
                       headers={"kid": kid_for(public_pem)})


def build_envelope(email: str, history_id: int, subscription: str) -> dict:
    """The Pub/Sub push body Gmail delivers (data is base64 of {emailAddress, historyId}).

    Gmail's canonical push-guide example encodes ``historyId`` as a QUOTED STRING
    (not a number), so we stringify it to match the wire shape.
    """
    data = json.dumps({"emailAddress": email, "historyId": str(history_id)}).encode()
    return {
        "message": {
            "data": base64.b64encode(data).decode(),
            "messageId": str(int(time.time() * 1000)),
            "publishTime": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        },
        "subscription": subscription,
    }


async def emit_push(consumer_url: str, *, private_pem: str, public_pem: str, audience: str,
                    push_sa_email: str, email: str, history_id: int, subscription: str) -> int:
    """Sign + POST a push to the consumer's webhook. Returns the HTTP status."""
    import httpx

    jwt_tok = sign_oidc(private_pem, public_pem, audience=audience, push_sa_email=push_sa_email)
    envelope = build_envelope(email, history_id, subscription)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(consumer_url, json=envelope,
                                 headers={"Authorization": f"Bearer {jwt_tok}"})
        return resp.status_code
