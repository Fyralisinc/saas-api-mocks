"""Service-account Bearer auth for the Grafana mock.

Grafana authenticates every HTTP API call with ``Authorization: Bearer
<token>``. The only credential is an **org-scoped service-account token**
(prefix ``glsa_``, deprecating the old API keys). The mock is single-tenant per
run; it accepts any non-empty Bearer and rejects a missing/blank one with
Grafana's ``{"message": "..."}`` error envelope at HTTP 401.
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
