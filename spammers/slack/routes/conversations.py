"""conversations.{info,list,history,replies,members} — channel surfaces."""
from __future__ import annotations

import json
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Request

from spammers.common.errors import slack_error
from spammers.common.pagination import slack_paginate
from spammers.slack.params import bool_param, int_param, read_params, str_param
from spammers.slack.responses import SlackJSONResponse as JSONResponse
from spammers.slack.auth import resolve_workspace
from spammers.slack.ratelimit import check as ratelimit_check
from spammers.slack.state import state


router = APIRouter()


async def _find_channel(workspace_pk: UUID, channel_id: str) -> Optional[dict]:
    st = state()
    row = await st.pool.fetchrow(
        """
        SELECT id, channel_id, name, is_private, is_archived, is_general, topic, purpose,
               creator_user_id, created_at
          FROM app_slack.channels
         WHERE workspace_id = $1
           AND (channel_id = $2 OR name = $2)
        """,
        workspace_pk, channel_id.lstrip("#"),
    )
    return dict(row) if row else None


def _channel_dto(row: dict, team_id: str, num_members: int = 0) -> dict:
    return {
        "id": row["channel_id"],
        "name": row["name"],
        "is_channel": not row["is_private"],
        "is_private": row["is_private"],
        "is_archived": row["is_archived"],
        "is_general": row["is_general"],
        "is_im": False,
        "is_mpim": False,
        "is_member": True,
        "is_org_shared": False,
        "is_shared": False,
        "is_pending_ext_shared": False,
        "created": int(row["created_at"].timestamp()),
        "creator": row.get("creator_user_id") or "",
        "topic": {"value": row["topic"] or "", "creator": "", "last_set": 0},
        "purpose": {"value": row["purpose"] or "", "creator": "", "last_set": 0},
        "num_members": num_members,
        "context_team_id": team_id,
    }


@router.post("/api/conversations.info")
@router.get("/api/conversations.info")
async def info(request: Request):
    ws = await resolve_workspace(request)
    if ws is None:
        return JSONResponse(slack_error("invalid_auth"))
    rl = await ratelimit_check("conversations.info", identity=ws["team_id"])
    if rl is not None:
        return rl
    params = await read_params(request)
    channel = str_param(params, "channel")
    if not channel:
        return JSONResponse(slack_error("channel_not_found"))
    row = await _find_channel(ws["id"], channel)
    if row is None:
        return JSONResponse(slack_error("channel_not_found"))
    return JSONResponse({"ok": True, "channel": _channel_dto(row, ws["team_id"])})


@router.post("/api/conversations.list")
@router.get("/api/conversations.list")
async def conv_list(request: Request):
    ws = await resolve_workspace(request)
    if ws is None:
        return JSONResponse(slack_error("invalid_auth"))
    rl = await ratelimit_check("conversations.list", identity=ws["team_id"])
    if rl is not None:
        return rl
    params = await read_params(request)
    cursor = str_param(params, "cursor")
    limit = int_param(params, "limit", 100, lo=1, hi=1000)
    types = params.get("types") or "public_channel,private_channel"
    exclude_archived = bool_param(params, "exclude_archived", False)
    type_set = set(t.strip() for t in types.split(",") if t.strip())
    where = ["workspace_id = $1"]
    args: list = [ws["id"]]
    if exclude_archived:
        where.append("is_archived = FALSE")
    if "public_channel" not in type_set and "private_channel" in type_set:
        where.append("is_private = TRUE")
    elif "private_channel" not in type_set and "public_channel" in type_set:
        where.append("is_private = FALSE")
    sql = f"""
        SELECT id, channel_id, name, is_private, is_archived, is_general, topic, purpose,
               creator_user_id, created_at
          FROM app_slack.channels
         WHERE {' AND '.join(where)}
         ORDER BY name ASC
    """
    rows = [dict(r) for r in await state().pool.fetch(sql, *args)]
    dtos = [_channel_dto(r, ws["team_id"]) for r in rows]
    page, meta = slack_paginate(dtos, cursor=cursor, limit=limit)
    body = {"ok": True, "channels": page}
    body.update(meta)
    return JSONResponse(body)


@router.post("/api/conversations.history")
@router.get("/api/conversations.history")
async def history(request: Request):
    ws = await resolve_workspace(request)
    if ws is None:
        return JSONResponse(slack_error("invalid_auth"))
    rl = await ratelimit_check("conversations.history", identity=ws["team_id"])
    if rl is not None:
        return rl
    params = await read_params(request)
    channel = str_param(params, "channel")
    cursor = str_param(params, "cursor")
    limit = int_param(params, "limit", 100, lo=1, hi=1000)
    oldest = str_param(params, "oldest")
    latest = str_param(params, "latest")
    inclusive = bool_param(params, "inclusive", False)
    if not channel:
        return JSONResponse(slack_error("channel_not_found"))
    chan = await _find_channel(ws["id"], channel)
    if chan is None:
        return JSONResponse(slack_error("channel_not_found"))

    where = ["channel_pk = $1", "is_hidden = FALSE", "thread_ts IS NULL"]
    args: list = [chan["id"]]
    if oldest is not None:
        args.append(oldest)
        where.append(f"ts > ${len(args)}" if not inclusive else f"ts >= ${len(args)}")
    if latest is not None:
        args.append(latest)
        where.append(f"ts < ${len(args)}" if not inclusive else f"ts <= ${len(args)}")

    sql = f"""
        SELECT m.ts, m.thread_ts, m.subtype, m.text, m.blocks, m.attachments,
               m.reply_count, m.reactions, m.edited,
               u.slack_user_id AS user_id
          FROM app_slack.messages m
          LEFT JOIN app_slack.users u ON u.id = m.user_pk
         WHERE {' AND '.join(where)}
         ORDER BY m.ts DESC
    """
    rows = await state().pool.fetch(sql, *args)
    msgs = [_msg_dto(dict(r), ws["team_id"]) for r in rows]
    page, meta = slack_paginate(msgs, cursor=cursor, limit=limit)
    has_more = bool(meta.get("response_metadata"))
    body = {"ok": True, "messages": page, "has_more": has_more,
            "pin_count": 0, "channel_actions_ts": None, "channel_actions_count": 0}
    body.update(meta)
    return JSONResponse(body)


@router.post("/api/conversations.replies")
@router.get("/api/conversations.replies")
async def replies(request: Request):
    ws = await resolve_workspace(request)
    if ws is None:
        return JSONResponse(slack_error("invalid_auth"))
    rl = await ratelimit_check("conversations.replies", identity=ws["team_id"])
    if rl is not None:
        return rl
    params = await read_params(request)
    channel = str_param(params, "channel")
    ts = str_param(params, "ts")
    cursor = str_param(params, "cursor")
    limit = int_param(params, "limit", 100, lo=1, hi=1000)
    if not channel:
        return JSONResponse(slack_error("channel_not_found"))
    if not ts:
        return JSONResponse(slack_error("thread_not_found"))
    chan = await _find_channel(ws["id"], channel)
    if chan is None:
        return JSONResponse(slack_error("channel_not_found"))

    sql = """
        SELECT m.ts, m.thread_ts, m.subtype, m.text, m.blocks, m.attachments,
               m.reply_count, m.reactions, m.edited,
               u.slack_user_id AS user_id
          FROM app_slack.messages m
          LEFT JOIN app_slack.users u ON u.id = m.user_pk
         WHERE m.channel_pk = $1 AND (m.ts = $2 OR m.thread_ts = $2)
         ORDER BY m.ts ASC
    """
    rows = await state().pool.fetch(sql, chan["id"], ts)
    msgs = [_msg_dto(dict(r), ws["team_id"]) for r in rows]
    page, meta = slack_paginate(msgs, cursor=cursor, limit=limit)
    has_more = bool(meta.get("response_metadata"))
    body = {"ok": True, "messages": page, "has_more": has_more}
    body.update(meta)
    return JSONResponse(body)


@router.post("/api/conversations.members")
@router.get("/api/conversations.members")
async def members(request: Request):
    ws = await resolve_workspace(request)
    if ws is None:
        return JSONResponse(slack_error("invalid_auth"))
    rl = await ratelimit_check("conversations.members", identity=ws["team_id"])
    if rl is not None:
        return rl
    params = await read_params(request)
    channel = str_param(params, "channel")
    cursor = str_param(params, "cursor")
    limit = int_param(params, "limit", 100, lo=1, hi=1000)
    if not channel:
        return JSONResponse(slack_error("channel_not_found"))
    chan = await _find_channel(ws["id"], channel)
    if chan is None:
        return JSONResponse(slack_error("channel_not_found"))

    # If channel_membership is populated, use it; otherwise list all workspace users
    rows = await state().pool.fetch(
        """
        SELECT u.slack_user_id AS id
          FROM app_slack.channel_membership cm
          JOIN app_slack.users u ON u.id = cm.user_pk
         WHERE cm.channel_pk = $1
        UNION
        SELECT u.slack_user_id AS id
          FROM app_slack.users u
         WHERE u.workspace_id = $2
           AND NOT EXISTS (SELECT 1 FROM app_slack.channel_membership WHERE channel_pk = $1 LIMIT 1)
         ORDER BY id ASC
        """,
        chan["id"], ws["id"],
    )
    ids = [r["id"] for r in rows]
    page, meta = slack_paginate(ids, cursor=cursor, limit=limit)
    body = {"ok": True, "members": page}
    body.update(meta)
    return JSONResponse(body)


def _msg_dto(row: dict, team_id: str) -> dict:
    dto: dict = {
        "type": "message",
        "user": row.get("user_id") or "",
        "text": row["text"],
        "ts": row["ts"],
        "team": team_id,
    }
    if row.get("thread_ts"):
        dto["thread_ts"] = row["thread_ts"]
        if row.get("reply_count"):
            dto["reply_count"] = row["reply_count"]
    elif row.get("reply_count"):
        # Thread parent: Slack reports thread_ts == its own ts plus reply_count.
        dto["thread_ts"] = row["ts"]
        dto["reply_count"] = row["reply_count"]
    if row.get("subtype"):
        dto["subtype"] = row["subtype"]
    if row.get("blocks"):
        dto["blocks"] = row["blocks"] if isinstance(row["blocks"], list) else json.loads(row["blocks"])
    if row.get("attachments"):
        dto["attachments"] = row["attachments"] if isinstance(row["attachments"], list) else json.loads(row["attachments"])
    if row.get("reactions"):
        rxns = row["reactions"]
        if not isinstance(rxns, list):
            rxns = json.loads(rxns)
        if rxns:
            dto["reactions"] = rxns
    if row.get("edited"):
        dto["edited"] = row["edited"] if isinstance(row["edited"], dict) else json.loads(row["edited"])
    return dto
