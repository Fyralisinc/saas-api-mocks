"""Drive files: list (backfill walk), metadata get, export, comments, revisions.

  GET /drive/v3/files                       full walk (corpora=user|drive)
  GET /drive/v3/files/{id}                  metadata, or alt=media -> raw body
  GET /drive/v3/files/{id}/export           Google-native doc -> text body
  GET /drive/v3/files/{id}/comments         comments (+ replies)
  GET /drive/v3/files/{id}/revisions        revision history
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from starlette.responses import PlainTextResponse, Response

from spammers.common.errors import google_error
from spammers.drive.dto import comment_dto, file_dto, revision_dto
from spammers.drive.responses import GoogleJSONResponse as JSONResponse
from spammers.drive.routes._common import (
    MY_DRIVE_SENTINEL,
    installation,
    require_claims,
    resolve_drive,
    unauthorized,
)
from spammers.drive.state import state
from spammers.drive.tokens import decode_offset, encode_offset

router = APIRouter()

_MODIFIED_AFTER = re.compile(r"modifiedTime\s*>\s*'([^']+)'")


def _parse_rfc3339(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


async def _file_row(st, inst_pk, file_id: str):
    return await st.pool.fetchrow(
        "SELECT f.*, d.drive_id, d.kind AS drive_kind FROM app_drive.files f JOIN app_drive.drives d ON d.id=f.drive_pk "
        "WHERE f.installation_pk=$1 AND f.file_id=$2",
        inst_pk, file_id,
    )


@router.get("/drive/v3/files")
async def list_files(request: Request):
    claims = require_claims(request)
    if claims is None:
        return unauthorized()
    st = state()
    inst = await installation(st)
    if inst is None:
        return JSONResponse({"kind": "drive#fileList", "incompleteSearch": False, "files": []})

    q = request.query_params
    drive_id = q.get("driveId") if q.get("corpora") == "drive" else None
    drive = await resolve_drive(st, inst["id"], drive_id=drive_id, sub=claims.get("sub"))
    if drive is None:
        return JSONResponse({"kind": "drive#fileList", "incompleteSearch": False, "files": []})

    qstr = q.get("q") or ""
    where = "drive_pk = $1"
    params: list = [drive["id"]]
    if "trashed = false" in qstr or "trashed=false" in qstr:
        where += " AND trashed = FALSE"
    m = _MODIFIED_AFTER.search(qstr)
    if m:
        after = _parse_rfc3339(m.group(1))
        if after is not None:
            params.append(after)
            where += f" AND modified_time > ${len(params)}"

    rows = await st.pool.fetch(
        f"SELECT f.*, d.drive_id, d.kind AS drive_kind FROM app_drive.files f JOIN app_drive.drives d ON d.id=f.drive_pk "
        f"WHERE {where} ORDER BY modified_time ASC, file_id ASC",
        *params,
    )
    try:
        # files.list caps pageSize at 100 ("values above 100 are changed to 100"),
        # NOT 1000 (that is changes.list's cap).
        page_size = max(1, min(int(q.get("pageSize", 100)), 100))
    except ValueError:
        page_size = 100
    offset = decode_offset(q.get("pageToken")) or 0
    page = rows[offset:offset + page_size]
    body = {"kind": "drive#fileList", "incompleteSearch": False,
            "files": [file_dto(dict(r)) for r in page]}
    if offset + page_size < len(rows):
        body["nextPageToken"] = encode_offset(offset + page_size)
    return JSONResponse(body)


@router.get("/drive/v3/files/{file_id}/export")
async def export_file(request: Request, file_id: str):
    if require_claims(request) is None:
        return unauthorized()
    st = state()
    inst = await installation(st)
    row = await _file_row(st, inst["id"], file_id) if inst else None
    if row is None:
        return JSONResponse(google_error(404, "File not found.", reason="notFound"), status_code=404)
    mime = request.query_params.get("mimeType", "text/plain")
    return PlainTextResponse(row["extracted_text"] or "", media_type=mime)


@router.get("/drive/v3/files/{file_id}/comments")
async def list_comments(request: Request, file_id: str):
    if require_claims(request) is None:
        return unauthorized()
    # The comments collection REQUIRES an explicit `fields` selector; real Drive
    # returns 400 when it is omitted (a documented quirk of comments/replies).
    if "fields" not in request.query_params:
        return JSONResponse(
            google_error(400, "The 'fields' parameter is required for this method.",
                         reason="required", location="fields", location_type="parameter"),
            status_code=400,
        )
    st = state()
    inst = await installation(st)
    row = await _file_row(st, inst["id"], file_id) if inst else None
    if row is None:
        return JSONResponse(google_error(404, "File not found.", reason="notFound"), status_code=404)
    crows = await st.pool.fetch(
        "SELECT * FROM app_drive.comments WHERE file_pk=$1 ORDER BY position, created_time", row["id"],
    )
    q = request.query_params
    try:
        page_size = max(1, min(int(q.get("pageSize", 100)), 100))
    except ValueError:
        page_size = 100
    offset = decode_offset(q.get("pageToken")) or 0
    page = crows[offset:offset + page_size]
    body = {"kind": "drive#commentList", "comments": [comment_dto(dict(r)) for r in page]}
    if offset + page_size < len(crows):
        body["nextPageToken"] = encode_offset(offset + page_size)
    return JSONResponse(body)


@router.get("/drive/v3/files/{file_id}/revisions")
async def list_revisions(request: Request, file_id: str):
    if require_claims(request) is None:
        return unauthorized()
    st = state()
    inst = await installation(st)
    row = await _file_row(st, inst["id"], file_id) if inst else None
    if row is None:
        return JSONResponse(google_error(404, "File not found.", reason="notFound"), status_code=404)
    rrows = await st.pool.fetch(
        "SELECT * FROM app_drive.revisions WHERE file_pk=$1 ORDER BY position, modified_time", row["id"],
    )
    q = request.query_params
    try:
        page_size = max(1, min(int(q.get("pageSize", 100)), 100))
    except ValueError:
        page_size = 100
    offset = decode_offset(q.get("pageToken")) or 0
    page = rrows[offset:offset + page_size]
    body = {"kind": "drive#revisionList", "revisions": [revision_dto(dict(r)) for r in page]}
    if offset + page_size < len(rrows):
        body["nextPageToken"] = encode_offset(offset + page_size)
    return JSONResponse(body)


@router.get("/drive/v3/files/{file_id}")
async def get_file(request: Request, file_id: str):
    if require_claims(request) is None:
        return unauthorized()
    st = state()
    inst = await installation(st)
    row = await _file_row(st, inst["id"], file_id) if inst else None
    if row is None:
        return JSONResponse(google_error(404, "File not found.", reason="notFound"), status_code=404)
    if request.query_params.get("alt") == "media":
        # Plain-text / binary download path. We only seed text-extractable bodies,
        # so return the stored text bytes (PDF bytes aren't synthesized).
        return Response(content=(row["extracted_text"] or "").encode("utf-8"),
                        media_type=row["mime_type"] or "application/octet-stream")
    return JSONResponse(file_dto(dict(row)))
