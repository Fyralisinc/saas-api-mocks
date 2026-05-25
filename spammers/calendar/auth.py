"""Bearer resolution for the Calendar mock.

The consumer presents the ``ya29.…`` access token minted by ``POST /token``.
We decode it (it self-encodes the impersonated subject + scope); no DB lookup is
needed since the token is run-agnostic and the calendarId in the path identifies
the calendar.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Request

from spammers.common.google_token import decode_access_token


def bearer(request: Request) -> Optional[str]:
    h = request.headers.get("authorization") or request.headers.get("Authorization")
    if not h:
        return None
    h = h.strip()
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    return h


def resolve_token(request: Request) -> Optional[dict]:
    """Return the token claims (``sub``, ``scope``, ``exp``) or None."""
    tok = bearer(request)
    if not tok:
        return None
    return decode_access_token(tok)
