"""Shared helpers for Jira routes."""
from __future__ import annotations

import base64
import json
from typing import Optional

from fastapi.responses import JSONResponse

from spammers.common.errors import jira_error


def unauthorized() -> JSONResponse:
    return JSONResponse(
        jira_error("Client must be authenticated to access this resource."),
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Jira"'},
    )


async def load_users(st, inst_pk) -> dict[str, dict]:
    rows = await st.pool.fetch(
        "SELECT account_id, email, display_name FROM app_jira.users WHERE installation_pk=$1",
        inst_pk,
    )
    return {r["account_id"]: dict(r) for r in rows}


def encode_token(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"o": offset}).encode()).decode().rstrip("=")


def decode_token(tok: Optional[str]) -> int:
    if not tok:
        return 0
    try:
        raw = base64.urlsafe_b64decode(tok + "=" * (-len(tok) % 4))
        return int(json.loads(raw)["o"])
    except Exception:
        try:
            return int(tok)  # tolerate a bare integer offset
        except Exception:
            return 0
