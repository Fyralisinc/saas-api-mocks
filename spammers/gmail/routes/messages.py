"""users.messages.list + users.messages.get."""
from __future__ import annotations

from fastapi import APIRouter, Request

from spammers.common.errors import gmail_error
from spammers.gmail.dto import message_dto, message_ref
from spammers.gmail.responses import GoogleJSONResponse as JSONResponse
from spammers.gmail.routes._helpers import require_mailbox
from spammers.gmail.state import state
from spammers.gmail.tokens import decode_page_token, encode_page_token

router = APIRouter()

_MAX = 500
_DEFAULT = 100


def _labels_match(row_labels, want: list[str]) -> bool:
    return set(want).issubset(set(row_labels or []))


@router.get("/gmail/v1/users/{user_id}/messages")
async def list_messages(request: Request, user_id: str):
    mbox, err = await require_mailbox(request, user_id)
    if err:
        return err
    qs = request.query_params
    try:
        max_results = int(qs.get("maxResults", _DEFAULT))
    except ValueError:
        max_results = _DEFAULT
    max_results = max(1, min(max_results, _MAX))
    want_labels = qs.getlist("labelIds")

    rows = await state().pool.fetch(
        """
        SELECT m.message_id, m.internal_date, m.label_ids, t.thread_id AS gmail_thread_id
          FROM app_gmail.messages m
          JOIN app_gmail.threads t ON t.id = m.thread_pk
         WHERE t.mailbox_pk = $1
         ORDER BY m.internal_date DESC, m.message_id DESC
        """,
        mbox["id"],
    )
    rows = [dict(r) for r in rows]
    if want_labels:
        import json
        rows = [r for r in rows
                if _labels_match(json.loads(r["label_ids"]) if isinstance(r["label_ids"], str) else r["label_ids"], want_labels)]

    offset = decode_page_token(qs.get("pageToken"))
    page = rows[offset:offset + max_results]
    has_more = offset + max_results < len(rows)
    body = {
        "messages": [message_ref(r) for r in page],
        "resultSizeEstimate": len(rows),
    }
    if has_more:
        body["nextPageToken"] = encode_page_token(offset + max_results)
    return JSONResponse(body)


@router.get("/gmail/v1/users/{user_id}/messages/{message_id}")
async def get_message(request: Request, user_id: str, message_id: str):
    mbox, err = await require_mailbox(request, user_id)
    if err:
        return err
    fmt = request.query_params.get("format", "full").lower()
    row = await state().pool.fetchrow(
        """
        SELECT m.*, t.thread_id AS gmail_thread_id
          FROM app_gmail.messages m
          JOIN app_gmail.threads t ON t.id = m.thread_pk
         WHERE t.mailbox_pk = $1 AND m.message_id = $2
        """,
        mbox["id"], message_id,
    )
    if row is None:
        return JSONResponse(gmail_error(404, "Requested entity was not found.", reason="notFound"),
                            status_code=404)
    return JSONResponse(message_dto(dict(row), fmt))
