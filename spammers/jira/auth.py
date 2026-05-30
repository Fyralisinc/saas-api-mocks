"""HTTP Basic auth for the Jira mock.

Jira Cloud authenticates with ``Authorization: Basic base64(email:api_token)``.
We decode it and match against this run's installation (one per run). A missing
or mismatched credential is a 401.
"""
from __future__ import annotations

import base64
from typing import Optional

import asyncpg
from fastapi import Request

from spammers.jira.state import state


def _decode_basic(request: Request) -> Optional[tuple[str, str]]:
    h = request.headers.get("authorization") or request.headers.get("Authorization")
    if not h or not h.lower().startswith("basic "):
        return None
    try:
        raw = base64.b64decode(h[6:].strip()).decode("utf-8", "replace")
    except Exception:
        return None
    if ":" not in raw:
        return None
    email, token = raw.split(":", 1)
    return email, token


async def authed_install(request: Request) -> Optional[asyncpg.Record]:
    """Return this run's installation iff the Basic credential matches it."""
    creds = _decode_basic(request)
    if creds is None:
        return None
    email, token = creds
    st = state()
    inst = await st.pool.fetchrow(
        "SELECT * FROM app_jira.installations WHERE run_id=$1", st.run_id,
    )
    if inst is None:
        return None
    if email == inst["account_email"] and token == inst["api_token"]:
        return inst
    return None
