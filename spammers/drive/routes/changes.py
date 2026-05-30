"""Drive changes feed — incremental sync.

  GET /drive/v3/changes/startPageToken  -> {startPageToken}  (the END position)
  GET /drive/v3/changes?pageToken=…      -> {changes, nextPageToken | newStartPageToken}

The changes log is the install's files ordered by ``change_seq``. The warm-start
token captured at backfill points PAST the last change, so a frozen run yields an
empty delta + ``newStartPageToken`` (the reconciler probe then converges — there
is genuinely nothing newer). ``pageToken`` is inclusive at-or-after its seq.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from spammers.common.errors import google_error
from spammers.drive.dto import change_dto
from spammers.drive.responses import GoogleJSONResponse as JSONResponse
from spammers.drive.routes._common import (
    MY_DRIVE_SENTINEL,
    installation,
    require_claims,
    unauthorized,
)
from spammers.drive.state import state
from spammers.drive.tokens import (
    decode_change_token,
    encode_change_token,
)

router = APIRouter()


async def _max_seq(st, inst_pk, drive_id: str | None) -> int:
    if drive_id and drive_id != MY_DRIVE_SENTINEL:
        v = await st.pool.fetchval(
            "SELECT MAX(f.change_seq) FROM app_drive.files f "
            "JOIN app_drive.drives d ON d.id=f.drive_pk "
            "WHERE f.installation_pk=$1 AND d.drive_id=$2",
            inst_pk, drive_id,
        )
    else:
        v = await st.pool.fetchval(
            "SELECT MAX(change_seq) FROM app_drive.files WHERE installation_pk=$1", inst_pk,
        )
    return int(v) if v is not None else 0


@router.get("/drive/v3/changes/startPageToken")
async def start_page_token(request: Request):
    if require_claims(request) is None:
        return unauthorized()
    st = state()
    inst = await installation(st)
    if inst is None:
        return JSONResponse({"kind": "drive#startPageToken", "startPageToken": encode_change_token(1)})
    drive_id = request.query_params.get("driveId")
    seq = await _max_seq(st, inst["id"], drive_id)
    return JSONResponse({"kind": "drive#startPageToken", "startPageToken": encode_change_token(seq + 1)})


@router.get("/drive/v3/changes")
async def list_changes(request: Request):
    if require_claims(request) is None:
        return unauthorized()
    st = state()
    q = request.query_params
    token = q.get("pageToken")
    seq = decode_change_token(token)
    if seq is None:
        return JSONResponse(
            google_error(400, "Invalid value for pageToken", reason="invalid",
                         location="pageToken", location_type="parameter"),
            status_code=400,
        )
    inst = await installation(st)
    if inst is None:
        return JSONResponse({"kind": "drive#changeList", "changes": [],
                             "newStartPageToken": encode_change_token(seq)})

    drive_id = q.get("driveId")
    try:
        page_size = max(1, min(int(q.get("pageSize", 200)), 1000))
    except ValueError:
        page_size = 200

    if drive_id and drive_id != MY_DRIVE_SENTINEL:
        rows = await st.pool.fetch(
            "SELECT f.*, d.drive_id, d.kind AS drive_kind FROM app_drive.files f "
            "JOIN app_drive.drives d ON d.id=f.drive_pk "
            "WHERE f.installation_pk=$1 AND d.drive_id=$2 AND f.change_seq >= $3 "
            "ORDER BY f.change_seq ASC",
            inst["id"], drive_id, seq,
        )
    else:
        rows = await st.pool.fetch(
            "SELECT f.*, d.drive_id, d.kind AS drive_kind FROM app_drive.files f "
            "JOIN app_drive.drives d ON d.id=f.drive_pk "
            "WHERE f.installation_pk=$1 AND f.change_seq >= $2 "
            "ORDER BY f.change_seq ASC",
            inst["id"], seq,
        )

    page = rows[:page_size]
    changes = []
    for r in page:
        rd = dict(r)
        rd["_removed"] = bool(rd.get("explicitly_trashed"))  # only hard-removed → removed=true
        changes.append(change_dto(rd))

    body: dict = {"kind": "drive#changeList", "changes": changes}
    if len(rows) > page_size:
        next_seq = int(page[-1]["change_seq"]) + 1
        body["nextPageToken"] = encode_change_token(next_seq)
    else:
        end = await _max_seq(st, inst["id"], drive_id)
        body["newStartPageToken"] = encode_change_token(end + 1)
    return JSONResponse(body)
