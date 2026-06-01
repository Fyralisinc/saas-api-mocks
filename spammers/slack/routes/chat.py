"""chat.postMessage / chat.update / chat.delete — Fyralis writing back to Slack.

Faithful to real Slack's argument validation and error surface: ``no_text`` when
nothing is sent, ``missing_scope`` without ``chat:write``, ``not_in_channel``
when the bot hasn't joined the target channel, and distinct channel-vs-message
errors on update/delete.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import asyncpg
from fastapi import APIRouter, Request

from spammers.common.errors import slack_error
from spammers.common.ids import bump_slack_ts, slack_ts
from spammers.slack.auth import has_scope, require_identity
from spammers.slack.ratelimit import check as ratelimit_check
from spammers.slack.responses import SlackJSONResponse as JSONResponse
from spammers.slack.routes.conversations import _find_channel, _visibility
from spammers.slack.state import state


router = APIRouter()


async def _body(request: Request) -> dict[str, Any]:
    ctype = (request.headers.get("content-type") or "").lower()
    if ctype.startswith("application/json"):
        return await request.json()
    form = await request.form()
    return {k: form[k] for k in form}


def _json_arg(body: dict, key: str):
    val = body.get(key)
    if isinstance(val, str):
        try:
            return json.loads(val)
        except ValueError:
            return None
    return val


@router.post("/api/chat.postMessage")
async def post_message(request: Request):
    ws, err = await require_identity(request)
    if err:
        return err
    body = await _body(request)
    channel_param = body.get("channel")
    if not channel_param:
        return JSONResponse(slack_error("channel_not_found"))

    # postMessage is throttled per channel (1 msg/sec/channel), so the bucket is
    # keyed on (workspace, channel).
    rl = await ratelimit_check("chat.postMessage", identity=f"{ws['team_id']}:{channel_param}")
    if rl is not None:
        return rl

    if not has_scope(ws, "chat:write"):
        return JSONResponse(slack_error("missing_scope", needed="chat:write",
                                        provided=",".join(ws.get("scopes") or [])))

    chan = await _find_channel(ws["id"], channel_param)
    if chan is None:
        return JSONResponse(slack_error("channel_not_found"))
    visible, is_member, _ = await _visibility(ws, chan)
    if not visible:
        return JSONResponse(slack_error("channel_not_found"))
    if not is_member:
        return JSONResponse(slack_error("not_in_channel"))

    text = body.get("text") or ""
    blocks = _json_arg(body, "blocks")
    attachments = _json_arg(body, "attachments")
    if not text and not blocks and not attachments:
        return JSONResponse(slack_error("no_text"))
    thread_ts = body.get("thread_ts")

    st = state()
    now_ts = await _insert_unique(
        st, chan["id"], text,
        json.dumps(blocks) if blocks else None,
        json.dumps(attachments) if attachments else None,
        thread_ts,
    )

    message: dict[str, Any] = {
        "type": "message",
        "subtype": "bot_message",
        "text": text,
        "ts": now_ts,
        "username": "Fyralis",
        "bot_id": ws["bot_id"],
        "app_id": ws["app_id"],
        "team": ws["team_id"],
    }
    if blocks:
        message["blocks"] = blocks
    if attachments:
        message["attachments"] = attachments
    return JSONResponse({"ok": True, "channel": chan["channel_id"], "ts": now_ts, "message": message})


async def _insert_unique(st, channel_pk, text, blocks, attachments, thread_ts) -> str:
    """Insert a bot message, guaranteeing a per-channel-unique ts.

    On a ts collision (two messages in the same microsecond) we bump the
    fractional part — mirroring how Slack disambiguates within a channel.
    """
    ts = slack_ts(datetime.now(timezone.utc))
    for _ in range(1000):
        try:
            await st.pool.execute(
                """
                INSERT INTO app_slack.messages
                    (id, channel_pk, user_pk, ts, thread_ts, subtype, text,
                     blocks, attachments, reply_count, reactions, is_hidden)
                VALUES ($1, $2, NULL, $3, $4, NULL, $5, $6::jsonb, $7::jsonb,
                        0, '[]'::jsonb, FALSE)
                """,
                uuid4(), channel_pk, ts, thread_ts, text, blocks, attachments,
            )
            return ts
        except asyncpg.exceptions.UniqueViolationError:
            ts = bump_slack_ts(ts)
    raise RuntimeError("could not allocate a unique ts")


@router.post("/api/chat.update")
async def chat_update(request: Request):
    ws, err = await require_identity(request)
    if err:
        return err
    rl = await ratelimit_check("chat.update", identity=ws["team_id"])
    if rl is not None:
        return rl
    if not has_scope(ws, "chat:write"):
        return JSONResponse(slack_error("missing_scope", needed="chat:write",
                                        provided=",".join(ws.get("scopes") or [])))
    body = await _body(request)
    channel = body.get("channel")
    ts = body.get("ts")
    text = body.get("text", "")
    chan = await _find_channel(ws["id"], channel) if channel else None
    if chan is None:
        return JSONResponse(slack_error("channel_not_found"))
    if not ts:
        return JSONResponse(slack_error("invalid_arguments"))
    st = state()
    row = await st.pool.fetchrow(
        "UPDATE app_slack.messages SET text = $3, edited = $4::jsonb "
        "WHERE channel_pk = $1 AND ts = $2 AND is_hidden = FALSE RETURNING ts",
        chan["id"], ts, text, json.dumps({"user": ws["bot_user_id"], "ts": ts}),
    )
    if row is None:
        return JSONResponse(slack_error("message_not_found"))
    return JSONResponse({
        "ok": True, "channel": chan["channel_id"], "ts": ts, "text": text,
        "message": {"type": "message", "text": text, "user": ws["bot_user_id"], "ts": ts},
    })


@router.post("/api/chat.delete")
async def chat_delete(request: Request):
    ws, err = await require_identity(request)
    if err:
        return err
    rl = await ratelimit_check("chat.delete", identity=ws["team_id"])
    if rl is not None:
        return rl
    if not has_scope(ws, "chat:write"):
        return JSONResponse(slack_error("missing_scope", needed="chat:write",
                                        provided=",".join(ws.get("scopes") or [])))
    body = await _body(request)
    channel = body.get("channel")
    ts = body.get("ts")
    chan = await _find_channel(ws["id"], channel) if channel else None
    if chan is None:
        return JSONResponse(slack_error("channel_not_found"))
    if not ts:
        return JSONResponse(slack_error("invalid_arguments"))
    st = state()
    row = await st.pool.fetchrow(
        "UPDATE app_slack.messages SET is_hidden = TRUE "
        "WHERE channel_pk = $1 AND ts = $2 AND is_hidden = FALSE RETURNING ts",
        chan["id"], ts,
    )
    if row is None:
        return JSONResponse(slack_error("message_not_found"))
    return JSONResponse({"ok": True, "channel": chan["channel_id"], "ts": ts})
