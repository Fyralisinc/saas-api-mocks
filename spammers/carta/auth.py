"""Bearer-token auth for the Carta mock.

Carta authenticates every read with an OAuth 2.0 access token presented as
``Authorization: Bearer <token>``. The token is opaque (Carta tokens are NOT JWTs)
and is minted at ``POST /o/access_token/`` (client-credentials grant); see app.py.
A missing/invalid token is **401** with the google.rpc.Status envelope (reason
``MISSING_OR_INVALID_ACCESS_TOKEN``); an insufficient-scope token would be **403**
(``INSUFFICIENT_SCOPE``) — the mock is single-tenant and accepts any non-empty
Bearer, so only the 401 path (missing/blank) is exercised. HTTP Basic is the
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
