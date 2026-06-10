"""Bearer-token auth for the Gusto mock.

Gusto authenticates every read with an OAuth 2.0 access token presented as
``Authorization: Bearer <token>`` (an opaque ~43-char URL-safe base64 string,
no fixed prefix). The token is minted at ``POST /oauth/token`` (authorization_code
or refresh_token grant — NOT client-credentials like Ramp); see app.py. A
missing/invalid token is **401** with ``category: "invalid_token"``. The mock is
single-tenant per run and accepts any non-empty Bearer (only the 401 missing/blank
path is exercised).

Every request MAY also carry the date-based API-version header
``X-Gusto-API-Version: YYYY-MM-DD`` (optional; the mock echoes it back, defaulting
to a pinned version when absent).
"""
from __future__ import annotations

from typing import Optional

from fastapi import Request

DEFAULT_API_VERSION = "2024-04-01"


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


def api_version(request: Request) -> str:
    """The requested ``X-Gusto-API-Version`` (echoed back), or the default."""
    return (request.headers.get("x-gusto-api-version")
            or request.headers.get("X-Gusto-API-Version")
            or DEFAULT_API_VERSION)
