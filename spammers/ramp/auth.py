"""Bearer-token auth for the Ramp mock.

Ramp authenticates every read with an OAuth 2.0 access token presented as
``Authorization: Bearer <token>`` (the access token is an opaque string with a
literal ``ramp_business_tok_`` prefix for client-credentials apps). The token is
minted at ``POST /developer/v1/token`` (client-credentials grant); see app.py.
A missing/invalid token is **401 Unauthorized**, a valid-but-insufficient-scope
token is **403 Forbidden** — the mock is single-tenant and accepts any non-empty
Bearer, so only the 401 path (missing/blank) is exercised. Basic auth is the
CLIENT-credential scheme for the token endpoint only, NOT for reads.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Request


def credential(request: Request) -> Optional[str]:
    """Extract the non-empty Bearer token, else None."""
    h = request.headers.get("authorization") or request.headers.get("Authorization")
    if not h:
        return None
    h = h.strip()
    if h.lower().startswith("bearer "):
        tok = h[7:].strip()
        return tok or None
    return None


def is_authed(request: Request) -> bool:
    return credential(request) is not None
