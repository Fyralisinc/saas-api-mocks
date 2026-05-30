"""Bearer resolution for the Drive mock.

The consumer presents the ``ya29.…`` access token minted by ``POST /token``
(shared DWD minter, same as Gmail/Calendar). We decode it for the impersonated
subject; ``corpora=user`` reads that subject's My Drive, ``corpora=drive`` reads
the addressed Shared Drive.
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
