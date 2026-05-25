"""users.threads.get — a thread with its full messages."""
from __future__ import annotations

from fastapi import APIRouter, Request

from spammers.common.errors import gmail_error
from spammers.gmail.dto import message_dto, thread_dto
from spammers.gmail.responses import GoogleJSONResponse as JSONResponse
from spammers.gmail.routes._helpers import require_mailbox
from spammers.gmail.state import state

router = APIRouter()


@router.get("/gmail/v1/users/{user_id}/threads/{thread_id}")
async def get_thread(request: Request, user_id: str, thread_id: str):
    mbox, err = await require_mailbox(request, user_id)
    if err:
        return err
    fmt = request.query_params.get("format", "full").lower()
    thread = await state().pool.fetchrow(
        "SELECT id FROM app_gmail.threads WHERE mailbox_pk = $1 AND thread_id = $2",
        mbox["id"], thread_id,
    )
    if thread is None:
        return JSONResponse(gmail_error(404, "Requested entity was not found.", reason="notFound"),
                            status_code=404)
    rows = await state().pool.fetch(
        """
        SELECT m.*, t.thread_id AS gmail_thread_id
          FROM app_gmail.messages m
          JOIN app_gmail.threads t ON t.id = m.thread_pk
         WHERE m.thread_pk = $1
         ORDER BY m.internal_date ASC, m.message_id ASC
        """,
        thread["id"],
    )
    msgs = [message_dto(dict(r), fmt if fmt != "minimal" else "metadata") for r in rows]
    hid = max((r["history_id"] for r in rows), default=1)
    return JSONResponse(thread_dto(thread_id, hid, msgs))
