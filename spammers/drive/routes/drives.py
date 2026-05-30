"""GET /drive/v3/drives — Shared Drive enumeration (useDomainAdminAccess)."""
from __future__ import annotations

from fastapi import APIRouter, Request

from spammers.drive.dto import drive_dto
from spammers.drive.responses import GoogleJSONResponse as JSONResponse
from spammers.drive.routes._common import installation, require_claims, unauthorized
from spammers.drive.state import state
from spammers.drive.tokens import decode_offset, encode_offset

router = APIRouter()


@router.get("/drive/v3/drives")
async def list_drives(request: Request):
    if require_claims(request) is None:
        return unauthorized()
    st = state()
    inst = await installation(st)
    if inst is None:
        return JSONResponse({"kind": "drive#driveList", "drives": []})

    rows = await st.pool.fetch(
        "SELECT * FROM app_drive.drives WHERE installation_pk=$1 AND kind='shared_drive' "
        "ORDER BY name, drive_id",
        inst["id"],
    )
    q = request.query_params
    try:
        page_size = max(1, min(int(q.get("pageSize", 100)), 100))
    except ValueError:
        page_size = 100
    offset = decode_offset(q.get("pageToken")) or 0
    page = rows[offset:offset + page_size]
    body = {"kind": "drive#driveList", "drives": [drive_dto(dict(r)) for r in page]}
    if offset + page_size < len(rows):
        body["nextPageToken"] = encode_offset(offset + page_size)
    return JSONResponse(body)
