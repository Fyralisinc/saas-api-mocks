"""Bearer-token auth for the Brex mock.

Brex authenticates every API call with a user/OAuth token presented as
``Authorization: Bearer <token>`` (user tokens carry a literal ``bxt_`` prefix;
OAuth access tokens are also Bearer). Per Brex's docs a missing/invalid token is
**401 Unauthorized** and a valid-but-insufficient-scope token is **403 Forbidden**
— the mock is single-tenant and accepts any non-empty Bearer token, so only the
401 path (missing/blank) is exercised. Basic auth is NOT a Brex scheme (unlike
mercury/ashby) — a non-Bearer Authorization header is treated as unauthenticated.
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
