"""Shared helpers for Drive routes: auth gate + target-drive resolution."""
from __future__ import annotations

from typing import Optional

import asyncpg
from fastapi import Request

from spammers.common.errors import google_error
from spammers.drive.auth import resolve_token
from spammers.drive.responses import GoogleJSONResponse as JSONResponse
from spammers.drive.state import state

MY_DRIVE_SENTINEL = "my-drive"


def unauthorized() -> JSONResponse:
    return JSONResponse(
        google_error(401, "Invalid Credentials", reason="authError",
                     location="Authorization", location_type="header"),
        status_code=401,
    )


def require_claims(request: Request) -> Optional[dict]:
    return resolve_token(request)


async def installation(st) -> Optional[asyncpg.Record]:
    return await st.pool.fetchrow(
        "SELECT * FROM app_drive.installations WHERE run_id = $1", st.run_id,
    )


async def resolve_drive(st, inst_pk, *, drive_id: Optional[str], sub: Optional[str]):
    """Resolve the target ``drives`` row.

    ``corpora=drive`` addresses a Shared Drive by ``drive_id``; otherwise the
    impersonated user's My Drive (matched by owner_email == the token subject).
    """
    if drive_id and drive_id != MY_DRIVE_SENTINEL:
        return await st.pool.fetchrow(
            "SELECT * FROM app_drive.drives WHERE installation_pk=$1 AND drive_id=$2",
            inst_pk, drive_id,
        )
    return await st.pool.fetchrow(
        "SELECT * FROM app_drive.drives WHERE installation_pk=$1 AND kind='my_drive' AND owner_email=$2",
        inst_pk, sub,
    )
