"""chat.postMessage — outbound message from Fyralis into a channel."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, Request

from spammers.common.errors import slack_error
from spammers.common.ids import slack_ts
from spammers.slack.auth import resolve_workspace
from spammers.slack.ratelimit import check as ratelimit_check
from spammers.slack.responses import SlackJSONResponse as JSONResponse
from spammers.slack.routes.conversations import _find_channel
from spammers.slack.state import state


def _bot_id(workspace: dict) -> str:
    """Real Slack bot ids use a ``B`` prefix, distinct from the bot's ``U`` user id."""
    uid = workspace["bot_user_id"]
    return "B" + uid[1:] if uid.startswith("U") else uid


router = APIRouter()


@router.post("/api/chat.postMessage")
async def post_message(request: Request):
    ws = await resolve_workspace(request)
    if ws is None:
        return JSONResponse(slack_error("invalid_auth"))
    rl = await ratelimit_check("chat.postMessage", identity=ws["team_id"])
    if rl is not None:
        return rl

    ctype = (request.headers.get("content-type") or "").lower()
    body: dict[str, Any]
    if ctype.startswith("application/json"):
        body = await request.json()
    else:
        form = await request.form()
        body = {k: form[k] for k in form}

    channel_param = body.get("channel")
    if not channel_param:
        return JSONResponse(slack_error("channel_not_found"))
    chan = await _find_channel(ws["id"], channel_param)
    if chan is None:
        return JSONResponse(slack_error("channel_not_found"))

    text = body.get("text") or ""
    blocks = body.get("blocks")
    if isinstance(blocks, str):
        try:
            blocks = json.loads(blocks)
        except ValueError:
            blocks = None
    attachments = body.get("attachments")
    if isinstance(attachments, str):
        try:
            attachments = json.loads(attachments)
        except ValueError:
            attachments = None
    thread_ts = body.get("thread_ts")

    now_ts = slack_ts(datetime.now(timezone.utc))
    msg_id = uuid4()
    st = state()
    await st.pool.execute(
        """
        INSERT INTO app_slack.messages
            (id, channel_pk, user_pk, ts, thread_ts, subtype, text, blocks, attachments,
             reply_count, reactions, is_hidden)
        VALUES ($1, $2, NULL, $3, $4, NULL, $5, $6::jsonb, $7::jsonb, 0, '[]'::jsonb, FALSE)
        """,
        msg_id, chan["id"], now_ts, thread_ts, text,
        json.dumps(blocks) if blocks else None,
        json.dumps(attachments) if attachments else None,
    )

    return JSONResponse({
        "ok": True,
        "channel": chan["channel_id"],
        "ts": now_ts,
        "message": {
            "type": "message",
            "subtype": "bot_message",
            "text": text,
            "ts": now_ts,
            "username": "Fyralis",
            "bot_id": _bot_id(ws),
            "team": ws["team_id"],
        },
    })


@router.post("/api/chat.update")
async def chat_update(request: Request):
    ws = await resolve_workspace(request)
    if ws is None:
        return JSONResponse(slack_error("invalid_auth"))
    rl = await ratelimit_check("chat.postMessage", identity=ws["team_id"])
    if rl is not None:
        return rl
    body = await request.json() if (request.headers.get("content-type") or "").startswith("application/json") else dict(await request.form())
    channel = body.get("channel")
    ts = body.get("ts")
    text = body.get("text", "")
    chan = await _find_channel(ws["id"], channel) if channel else None
    if not chan or not ts:
        return JSONResponse(slack_error("message_not_found"))
    st = state()
    row = await st.pool.fetchrow(
        "UPDATE app_slack.messages SET text = $3, edited = $4::jsonb WHERE channel_pk = $1 AND ts = $2 RETURNING ts",
        chan["id"], ts, text, json.dumps({"user": ws["bot_user_id"], "ts": ts}),
    )
    if row is None:
        return JSONResponse(slack_error("message_not_found"))
    return JSONResponse({"ok": True, "channel": chan["channel_id"], "ts": ts, "text": text})


@router.post("/api/chat.delete")
async def chat_delete(request: Request):
    ws = await resolve_workspace(request)
    if ws is None:
        return JSONResponse(slack_error("invalid_auth"))
    rl = await ratelimit_check("chat.postMessage", identity=ws["team_id"])
    if rl is not None:
        return rl
    body = await request.json() if (request.headers.get("content-type") or "").startswith("application/json") else dict(await request.form())
    channel = body.get("channel")
    ts = body.get("ts")
    chan = await _find_channel(ws["id"], channel) if channel else None
    if not chan or not ts:
        return JSONResponse(slack_error("message_not_found"))
    st = state()
    row = await st.pool.fetchrow(
        "UPDATE app_slack.messages SET is_hidden = TRUE WHERE channel_pk = $1 AND ts = $2 RETURNING ts",
        chan["id"], ts,
    )
    if row is None:
        return JSONResponse(slack_error("message_not_found"))
    return JSONResponse({"ok": True, "channel": chan["channel_id"], "ts": ts})
