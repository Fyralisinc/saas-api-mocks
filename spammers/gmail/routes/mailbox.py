"""users.watch / users.stop / users.getProfile."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import Response

from spammers.gmail.dto import profile_dto
from spammers.gmail.responses import GoogleJSONResponse as JSONResponse
from spammers.gmail.routes._helpers import require_mailbox
from spammers.gmail.state import state

router = APIRouter()


@router.get("/gmail/v1/users/{user_id}/profile")
async def get_profile(request: Request, user_id: str):
    mbox, err = await require_mailbox(request, user_id)
    if err:
        return err
    profile = mbox["profile"]
    import json
    if isinstance(profile, str):
        profile = json.loads(profile)
    return JSONResponse(profile_dto(
        mbox["email"],
        int(profile.get("messagesTotal", 0)) if profile else 0,
        int(profile.get("threadsTotal", 0)) if profile else 0,
        mbox["history_id"],
    ))


@router.post("/gmail/v1/users/{user_id}/watch")
async def watch(request: Request, user_id: str):
    mbox, err = await require_mailbox(request, user_id)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    topic = body.get("topicName") or "projects/spammer/topics/gmail"
    label_ids = body.get("labelIds") or []
    label_action = body.get("labelFilterAction") or "include"
    import json
    expiration = datetime.now(timezone.utc) + timedelta(days=7)
    st = state()
    await st.pool.execute(
        """
        INSERT INTO app_gmail.watches
            (id, mailbox_pk, topic_name, label_ids, label_filter_action, expiration, started_history_id)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
        ON CONFLICT (mailbox_pk, topic_name)
        DO UPDATE SET label_ids = EXCLUDED.label_ids, expiration = EXCLUDED.expiration,
                      started_history_id = EXCLUDED.started_history_id
        """,
        uuid4(), mbox["id"], topic, json.dumps(label_ids), label_action,
        expiration, mbox["history_id"],
    )
    return JSONResponse({
        "historyId": str(mbox["history_id"]),
        "expiration": str(int(expiration.timestamp() * 1000)),
    })


@router.post("/gmail/v1/users/{user_id}/stop")
async def stop(request: Request, user_id: str):
    mbox, err = await require_mailbox(request, user_id)
    if err:
        return err
    await state().pool.execute("DELETE FROM app_gmail.watches WHERE mailbox_pk = $1", mbox["id"])
    return Response(status_code=204)
