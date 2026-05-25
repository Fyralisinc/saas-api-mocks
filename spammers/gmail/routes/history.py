"""users.history.list — incremental drain from a bookmark historyId."""
from __future__ import annotations

import json

from fastapi import APIRouter, Request

from spammers.common.errors import gmail_error
from spammers.gmail.dto import history_record
from spammers.gmail.responses import GoogleJSONResponse as JSONResponse
from spammers.gmail.routes._helpers import require_mailbox
from spammers.gmail.state import state
from spammers.gmail.tokens import decode_page_token, encode_page_token

router = APIRouter()


@router.get("/gmail/v1/users/{user_id}/history")
async def list_history(request: Request, user_id: str):
    mbox, err = await require_mailbox(request, user_id)
    if err:
        return err
    qs = request.query_params
    start = qs.get("startHistoryId")
    if not start:
        return JSONResponse(gmail_error(400, "startHistoryId is required.", reason="badRequest"),
                            status_code=400)
    try:
        start_hid = int(start)
    except ValueError:
        return JSONResponse(gmail_error(400, "Invalid startHistoryId.", reason="badRequest"),
                            status_code=400)
    try:
        max_results = int(qs.get("maxResults", 100))
    except ValueError:
        max_results = 100
    max_results = max(1, min(max_results, 500))

    rows = await state().pool.fetch(
        """
        SELECT history_id, message_id, thread_id, label_ids
          FROM app_gmail.history
         WHERE mailbox_pk = $1 AND history_id > $2
         ORDER BY history_id ASC
        """,
        mbox["id"], start_hid,
    )
    # Group consecutive rows by history_id into records.
    grouped: dict[int, list] = {}
    order: list[int] = []
    for r in rows:
        hid = r["history_id"]
        if hid not in grouped:
            grouped[hid] = []
            order.append(hid)
        labels = r["label_ids"]
        if isinstance(labels, str):
            labels = json.loads(labels)
        grouped[hid].append({"id": r["message_id"], "threadId": r["thread_id"], "labelIds": labels})

    records = [history_record(hid, grouped[hid]) for hid in order]
    offset = decode_page_token(qs.get("pageToken"))
    page = records[offset:offset + max_results]
    has_more = offset + max_results < len(records)

    body: dict = {"historyId": str(mbox["history_id"])}
    if page:
        body["history"] = page
    if has_more:
        body["nextPageToken"] = encode_page_token(offset + max_results)
    return JSONResponse(body)
