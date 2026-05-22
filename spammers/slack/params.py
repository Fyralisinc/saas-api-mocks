"""Slack Web API argument reading.

Every Slack Web API argument may be supplied via the query string OR the
request body (``application/x-www-form-urlencoded`` or, for some clients,
JSON). The official SDKs (``slack_sdk`` / ``@slack/web-api``) default to a
POST with a form-encoded body, so a route that binds only FastAPI query
params silently sees nothing — ``conversations.history`` then returns no
``channel`` and 422s instead of the real ``messages`` payload.

This merges both sources so the mock behaves like the real API regardless
of how the caller passes arguments. Body values win over query values when
both are present (a caller that bothers to send a body means it).
"""
from __future__ import annotations

import json
from typing import Optional
from urllib.parse import parse_qsl

from fastapi import Request


async def read_params(request: Request) -> dict[str, str]:
    params: dict[str, str] = dict(request.query_params)
    if request.method == "POST":
        ctype = (request.headers.get("content-type") or "").lower()
        body = await request.body()
        if ctype.startswith("application/json"):
            try:
                data = json.loads(body or b"{}")
            except Exception:
                data = None
            if isinstance(data, dict):
                params.update({k: str(v) for k, v in data.items() if v is not None})
        else:
            # x-www-form-urlencoded (slack_sdk's default). Parsed directly off
            # the raw body so we don't drag in python-multipart, matching
            # oauth._parse_body's approach.
            params.update(dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True)))
    return params


def int_param(
    params: dict[str, str], key: str, default: int, *, lo: int, hi: int
) -> int:
    """Parse an integer argument, clamping to ``[lo, hi]``.

    Slack clamps out-of-range ``limit`` values rather than rejecting them,
    so we do the same (a non-numeric value falls back to ``default``).
    """
    raw = params.get(key)
    if raw is None or raw == "":
        return default
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, val))


def str_param(params: dict[str, str], key: str) -> Optional[str]:
    val = params.get(key)
    return val if val not in (None, "") else None


def bool_param(params: dict[str, str], key: str, default: bool) -> bool:
    raw = params.get(key)
    if raw is None:
        return default
    return str(raw).lower() in ("1", "true", "yes", "t")
