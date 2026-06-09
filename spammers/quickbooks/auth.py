"""OAuth Bearer auth for the QuickBooks Online mock.

QBO authenticates every Accounting API call with ``Authorization: Bearer
<access_token>`` (OAuth 2.0). The realm in the path identifies the company; the
mock is single-tenant per run and does not have a provisioned token to match, so
it accepts any non-empty Bearer and rejects a missing/blank one with QBO's
``Fault`` envelope (type AuthenticationFailed, code 3200).
"""
from __future__ import annotations

from typing import Optional

from fastapi import Request


def bearer_token(request: Request) -> Optional[str]:
    h = request.headers.get("authorization") or request.headers.get("Authorization")
    if not h:
        return None
    h = h.strip()
    if h.lower().startswith("bearer "):
        tok = h[7:].strip()
        return tok or None
    return None


def is_authed(request: Request) -> bool:
    return bearer_token(request) is not None
