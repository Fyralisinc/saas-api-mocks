"""Bearer-token auth for the Deel mock.

Deel authenticates every REST call with a long-lived API token (an Organization
or Personal token) presented as ``Authorization: Bearer <token>``. Per Deel's
docs a missing/invalid token is **401 Unauthorized** and a valid-but-insufficient
-scope token is **403 Forbidden** — the mock is single-tenant and accepts any
non-empty Bearer token, so only the 401 path (missing/blank) is exercised. Basic
auth is NOT a Deel scheme — a non-Bearer Authorization header is unauthenticated.
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
