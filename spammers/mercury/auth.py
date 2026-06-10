"""API-token auth for the Mercury mock.

Mercury authenticates every API call with the org's API token, accepted **two**
ways (both documented):

  * ``Authorization: Bearer <token>``
  * ``Authorization: Basic base64(<token>:)`` — the token as the Basic *username*
    with an EMPTY password.

The token itself carries a literal ``secret-token:`` prefix (e.g.
``secret-token:mercury_production_…``). The mock is single-tenant per run and
does not match a provisioned token; it accepts any non-empty credential and
rejects a missing/blank one with HTTP 401. (Mercury's official docs do not
publish the 401 body shape — see app.py's ``_error``; treated as UNCONFIRMED.)
"""
from __future__ import annotations

import base64
import binascii
from typing import Optional

from fastapi import Request


def credential(request: Request) -> Optional[str]:
    """Extract the non-empty API token from a Bearer or Basic header, else None."""
    h = request.headers.get("authorization") or request.headers.get("Authorization")
    if not h:
        return None
    h = h.strip()
    low = h.lower()
    if low.startswith("bearer "):
        tok = h[7:].strip()
        return tok or None
    if low.startswith("basic "):
        raw = h[6:].strip()
        try:
            decoded = base64.b64decode(raw).decode("utf-8", "replace")
        except (binascii.Error, ValueError):
            return None
        # Basic auth: token is the username, password is empty -> "token:"
        user = decoded.split(":", 1)[0]
        return user or None
    return None


def is_authed(request: Request) -> bool:
    return credential(request) is not None
