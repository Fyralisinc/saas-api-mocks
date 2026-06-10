"""Auth for the Miro mock — ``Authorization: Bearer <access_token>`` only.

Miro's REST v2 read endpoints authenticate with a single long-lived org-level app
Bearer token (scope ``boards:read``):

    Authorization: Bearer <access_token>

The mock is single-tenant per run: it does not match a provisioned token, it
accepts any NON-EMPTY Bearer token, and treats a missing/blank one as
unauthenticated. The caller turns that into Miro's documented auth failure —
**401** with ``code: "tokenNotProvided"`` and the ``{status, code, message, type}``
error envelope (Miro's published OpenAPI ``Error401``). Unlike Figma there is NO
``X-…-Token`` apiKey header — Bearer is the only accepted scheme.
"""
from __future__ import annotations

from typing import Optional


def access_token(request) -> Optional[str]:
    """Return the presented Bearer token, or None when missing/blank."""
    h = request.headers.get("authorization") or request.headers.get("Authorization")
    if h:
        h = h.strip()
        if h.lower().startswith("bearer "):
            tok = h[7:].strip()
            if tok:
                return tok
    return None


def is_authed(request) -> bool:
    return access_token(request) is not None
