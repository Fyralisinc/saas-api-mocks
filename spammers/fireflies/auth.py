"""Bearer-token auth for the Fireflies mock.

Fireflies authenticates every GraphQL call with a long-lived API key presented as
``Authorization: Bearer <api_key>`` (the "Bearer archetype" — no OAuth bounce, no
refresh). A missing/blank/malformed header is the documented ``auth_failed`` error
(the docs: *"ensure that you are including the Authorization header with the word
Bearer and your API key"*). The mock is single-tenant per run and accepts any
non-empty Bearer; a missing/blank one fails as ``auth_failed``.
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
