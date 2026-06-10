"""HTTP Basic auth for the Ashby mock.

Ashby authenticates every request with a long-lived **API key presented as the
HTTP Basic username with an EMPTY password** — i.e.
``Authorization: Basic base64("<apiKey>:")`` (note the trailing colon). This is
the only documented scheme — there is no Bearer form, no OAuth, no refresh token
(Ashby's first-party docs, confirmed; the Brex/Jira archetype but with an empty
password rather than ``email:token``).

The mock is single-tenant per run: it does not match a provisioned key, it
accepts any non-empty Basic username and rejects a missing/blank credential with
HTTP **401** (Ashby returns 401 for a missing key, 403 for a wrong/deactivated
one — since the mock accepts any non-empty key there is no "wrong key" case, so
the only HTTP-auth failure is the 401). Ashby's docs call the auth-failure body
"a human-readable response" without pinning an exact JSON shape — see app.py's
``_error`` (treated as UNCONFIRMED; only the HTTP status is contractually firm).
"""
from __future__ import annotations

import base64
import binascii
from typing import Optional

from fastapi import Request


def api_key(request: Request) -> Optional[str]:
    """Extract the non-empty API key from the Basic ``Authorization`` header, else None.

    Bearer is deliberately NOT accepted — Ashby is Basic-only; a Bearer header is
    not the Ashby scheme and authenticates as missing.
    """
    h = request.headers.get("authorization") or request.headers.get("Authorization")
    if not h:
        return None
    h = h.strip()
    if not h.lower().startswith("basic "):
        return None
    raw = h[6:].strip()
    try:
        decoded = base64.b64decode(raw).decode("utf-8", "replace")
    except (binascii.Error, ValueError):
        return None
    # Basic auth: the API key is the username, the password is empty -> "<key>:".
    user = decoded.split(":", 1)[0]
    return user or None


def is_authed(request: Request) -> bool:
    return api_key(request) is not None
