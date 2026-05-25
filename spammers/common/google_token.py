"""Domain-wide-delegation (DWD) bearer tokens for the Google mocks.

The consumer mints a per-user, scope-bound bearer the real Google way: it signs
a service-account JWT (``iss``=SA email, ``sub``=impersonated user, ``scope``,
``aud``=token URI) and POSTs ``grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer``
with ``assertion=<jwt>`` to the token endpoint. The mock isn't a security
boundary (it never holds the consumer's SA private key), so it does **not**
verify the assertion signature — it decodes the claims, mints an opaque
``ya29.…`` access token that *encodes* the impersonated subject + scope, and
returns it in Google's token-response shape.

Tokens are stateless: any Google mock (Gmail :7004, Calendar :7005) can both
mint and decode them, so a single ``GOOGLE_OAUTH_TOKEN_URL`` can point at either
process and the other still accepts the resulting bearer.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Optional

import jwt as _jwt

# Shared, non-secret signing key — this is a mock, not an auth boundary. It only
# makes a hand-forged token implausible by accident; it protects nothing real.
_TOKEN_KEY = b"spammers-google-dwd-mock-v1"
_DEFAULT_TTL = 3600  # Google access tokens live ~1h (the consumer caches ~50m).


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def mint_access_token(sub: str, scope: str, *, ttl: int = _DEFAULT_TTL) -> tuple[str, int]:
    """Mint an opaque ``ya29.…`` access token. Returns ``(token, expires_in)``."""
    claims = {"sub": sub, "scope": scope, "exp": int(time.time()) + ttl}
    payload = _b64u(json.dumps(claims, separators=(",", ":")).encode())
    tag = _b64u(hmac.new(_TOKEN_KEY, payload.encode(), hashlib.sha256).digest()[:18])
    return f"ya29.{payload}.{tag}", ttl


def decode_access_token(token: str) -> Optional[dict]:
    """Return ``{sub, scope, exp}`` for a valid, unexpired token, else None."""
    if not token or not token.startswith("ya29."):
        return None
    parts = token[len("ya29."):].split(".")
    if len(parts) != 2:
        return None
    payload, tag = parts
    expected = _b64u(hmac.new(_TOKEN_KEY, payload.encode(), hashlib.sha256).digest()[:18])
    if not hmac.compare_digest(expected, tag):
        return None
    try:
        claims = json.loads(_b64u_decode(payload))
    except Exception:
        return None
    if int(claims.get("exp", 0)) < int(time.time()):
        return None
    return claims


def read_assertion(assertion: str) -> dict:
    """Decode the consumer's DWD JWT *without* verifying its signature.

    Real Google verifies the SA signature; the mock can't (it has no SA private
    key) and doesn't need to. We only need ``sub``/``scope`` to bind the token.
    """
    try:
        return _jwt.decode(assertion, options={"verify_signature": False})
    except Exception:
        return {}


def token_response(sub: str, scope: str, *, ttl: int = _DEFAULT_TTL) -> dict:
    """Google's ``/token`` success body for a DWD exchange."""
    token, expires_in = mint_access_token(sub, scope, ttl=ttl)
    return {"access_token": token, "token_type": "Bearer", "expires_in": expires_in, "scope": scope}
