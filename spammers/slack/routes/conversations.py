"""conversations.{info,list,history,replies,members} — channel + DM surfaces.

This is where the two-token model is enforced. A **bot token** (xoxb) reads
public/private channels it is a member of and CANNOT see human-human DMs. A
**user token** (xoxp) reads the consenting human's own 1:1 DMs (``im``) and
group DMs (``mpim``). Object shapes differ by type: an ``im`` carries a ``user``
(the counterpart) and no name/topic/purpose; an ``mpim`` carries an ``mpdm-…``
name. Visibility, membership errors, and per-object shape all key off the
resolved principal.
"""
from __future__ import annotations

import base64
import json
import re
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Request

from spammers.common.errors import slack_error
from spammers.common.pagination import slack_paginate
from spammers.slack.params import bool_param, int_param, read_params, str_param
from spammers.slack.responses import SlackJSONResponse as JSONResponse
from spammers.slack.auth import require_identity
from spammers.slack.ratelimit import check as ratelimit_check
from spammers.slack.state import state


router = APIRouter()

_TS_RE = re.compile(r"^\d+(\.\d+)?$")


# ---------------------------------------------------------------------------
# Membership / participant model
# ---------------------------------------------------------------------------

async def _participants(channel_pk: UUID) -> dict[str, str]:
    """Slack-user-id → handle for every participant of a channel.

    Participants = explicit channel_membership rows UNION the distinct authors
    of messages in the channel (so DM visibility works even when membership was
    not separately seeded).
    """
    rows = await state().pool.fetch(
        """
        SELECT DISTINCT u.slack_user_id AS id, p.handle AS handle
          FROM app_slack.users u
          JOIN org.people p ON p.id = u.person_id
         WHERE u.id IN (
                 SELECT user_pk FROM app_slack.channel_membership WHERE channel_pk = $1
                 UNION
                 SELECT user_pk FROM app_slack.messages
                  WHERE channel_pk = $1 AND user_pk IS NOT NULL
               )
        """,
        channel_pk,
    )
    return {r["id"]: r["handle"] for r in rows}


async def _visibility(ident: dict, chan: dict) -> tuple[bool, bool, dict[str, str]]:
    """Return (visible, is_member, participants) for ``ident`` on ``chan``.

    ``visible`` = may appear in conversations.list / be resolved at all.
    ``is_member`` = may read history (else not_in_channel for channels, or the
    DM is simply invisible).
    """
    is_dm = bool(chan.get("is_im") or chan.get("is_mpim"))
    if is_dm:
        parts = await _participants(chan["id"])
        if ident["token_type"] == "user":
            member = ident["acting_user_id"] in parts
            return member, member, parts
        # Bot tokens cannot read human-human DMs.
        return False, False, parts
    # Non-DM channel: a bot reads it only if invited (bot_is_member); a user
    # token reads channels it participates in.
    if ident["token_type"] == "bot":
        return True, bool(chan.get("bot_is_member", True)), {}
    parts = await _participants(chan["id"])
    member = ident["acting_user_id"] in parts
    return True, member, parts


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------

def _mpdm_name(parts: dict[str, str], stored: Optional[str]) -> str:
    if parts:
        handles = sorted(parts.values())
        return "mpdm-" + "--".join(handles) + "-1"
    return stored or "mpdm-1"


def _channel_dto(
    row: dict, team_id: str, ident: dict, is_member: bool,
    parts: dict[str, str], *, num_members: Optional[int] = None,
) -> dict:
    """Build the conversation object, branching on type as real Slack does."""
    is_im = bool(row.get("is_im"))
    is_mpim = bool(row.get("is_mpim"))
    created = int(row["created_at"].timestamp())

    if is_im:
        # 1:1 DM — minimal object with the counterpart `user`, no name/topic.
        counterpart = next(
            (uid for uid in parts if uid != ident.get("acting_user_id")), ""
        )
        return {
            "id": row["channel_id"],
            "created": created,
            "is_im": True,
            "is_org_shared": False,
            "user": counterpart,
            "is_user_deleted": False,
            "priority": 0,
            "context_team_id": team_id,
        }

    dto = {
        "id": row["channel_id"],
        "name": _mpdm_name(parts, row["name"]) if is_mpim else row["name"],
        "is_channel": not (row["is_private"] or is_mpim),
        "is_group": bool(row["is_private"]) and not is_mpim,
        "is_im": False,
        "is_mpim": is_mpim,
        "is_private": bool(row["is_private"]) or is_mpim,
        "is_archived": row["is_archived"],
        "is_general": row["is_general"] and not is_mpim,
        "is_member": is_member,
        "is_org_shared": False,
        "is_shared": False,
        "is_pending_ext_shared": False,
        "created": created,
        "creator": row.get("creator_user_id") or "",
        "context_team_id": team_id,
    }
    if not is_mpim:
        dto["topic"] = {"value": row["topic"] or "", "creator": "", "last_set": 0}
        dto["purpose"] = {"value": row["purpose"] or "", "creator": "", "last_set": 0}
    if num_members is not None:
        dto["num_members"] = num_members
    return dto


def _msg_dto(row: dict, team_id: str) -> dict:
    dto: dict = {
        "type": "message",
        "user": row.get("user_id") or "",
        "text": row["text"],
        "ts": row["ts"],
    }
    # Real Slack stamps `team` only on human-authored, non-subtype messages.
    if row.get("user_id") and not row.get("subtype"):
        dto["team"] = team_id
    if row.get("client_msg_id"):
        dto["client_msg_id"] = row["client_msg_id"]
    if row.get("thread_ts"):
        dto["thread_ts"] = row["thread_ts"]
        if row.get("reply_count"):
            dto["reply_count"] = row["reply_count"]
    elif row.get("reply_count"):
        # Thread parent: thread_ts == its own ts, plus the reply roll-up fields
        # real Slack returns on parents.
        dto["thread_ts"] = row["ts"]
        dto["reply_count"] = row["reply_count"]
        dto["reply_users_count"] = row.get("reply_users_count") or 0
        if row.get("latest_reply"):
            dto["latest_reply"] = row["latest_reply"]
        ru = row.get("reply_users")
        if ru:
            dto["reply_users"] = ru if isinstance(ru, list) else json.loads(ru)
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


async def _find_channel(workspace_pk: UUID, channel_id: str) -> Optional[dict]:
    row = await state().pool.fetchrow(
        """
        SELECT id, channel_id, name, is_private, is_archived, is_general,
               is_im, is_mpim, bot_is_member, topic, purpose, creator_user_id, created_at
          FROM app_slack.channels
         WHERE workspace_id = $1
           AND (channel_id = $2 OR name = $2)
        """,
        workspace_pk, channel_id.lstrip("#"),
    )
    return dict(row) if row else None


def _bad_cursor(cursor: Optional[str]) -> bool:
    if not cursor:
        return False
    try:
        json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
        return False
    except Exception:
        return True


def _history_limit(ident: dict, params: dict) -> tuple[int, JSONResponse | None]:
    """Effective limit for history/replies given the app class.

    Non-Marketplace apps (post 2025-05-29) are capped to 15 objects/page on
    conversations.history/replies; Marketplace/internal keep up to 1000.
    """
    non_mkt = ident.get("app_distribution") != "marketplace"
    default = 15 if non_mkt else 100
    hi = 15 if non_mkt else 1000
    return int_param(params, "limit", default, lo=1, hi=hi), None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/api/conversations.info")
@router.get("/api/conversations.info")
async def info(request: Request):
    ws, err = await require_identity(request)
    if err:
        return err
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
    visible, is_member, parts = await _visibility(ws, row)
    if not visible:
        return JSONResponse(slack_error("channel_not_found"))
    num_members = None
    if bool_param(params, "include_num_members", False):
        num_members = len(parts) if parts else await _member_count(row["id"])
    return JSONResponse({
        "ok": True,
        "channel": _channel_dto(row, ws["team_id"], ws, is_member, parts,
                                num_members=num_members),
    })


@router.post("/api/conversations.list")
@router.get("/api/conversations.list")
async def conv_list(request: Request):
    ws, err = await require_identity(request)
    if err:
        return err
    rl = await ratelimit_check("conversations.list", identity=ws["team_id"])
    if rl is not None:
        return rl
    params = await read_params(request)
    cursor = str_param(params, "cursor")
    if _bad_cursor(cursor):
        return JSONResponse(slack_error("invalid_cursor"))
    limit = int_param(params, "limit", 100, lo=1, hi=1000)
    # Real Slack default is public_channel ONLY.
    types = params.get("types") or "public_channel"
    exclude_archived = bool_param(params, "exclude_archived", False)
    type_set = set(t.strip() for t in types.split(",") if t.strip())

    where = ["workspace_id = $1"]
    args: list = [ws["id"]]
    if exclude_archived:
        where.append("is_archived = FALSE")
    flag_clauses: list[str] = []
    if "public_channel" in type_set:
        flag_clauses.append("(is_private = FALSE AND is_im = FALSE AND is_mpim = FALSE)")
    if "private_channel" in type_set:
        flag_clauses.append("(is_private = TRUE AND is_im = FALSE AND is_mpim = FALSE)")
    if "im" in type_set:
        flag_clauses.append("(is_im = TRUE)")
    if "mpim" in type_set:
        flag_clauses.append("(is_mpim = TRUE)")
    if flag_clauses:
        where.append("(" + " OR ".join(flag_clauses) + ")")
    sql = f"""
        SELECT id, channel_id, name, is_private, is_archived, is_general,
               is_im, is_mpim, bot_is_member, topic, purpose, creator_user_id, created_at
          FROM app_slack.channels
         WHERE {' AND '.join(where)}
         ORDER BY name ASC
    """
    rows = [dict(r) for r in await state().pool.fetch(sql, *args)]
    dtos = []
    for r in rows:
        visible, is_member, parts = await _visibility(ws, r)
        if not visible:
            continue
        dtos.append(_channel_dto(r, ws["team_id"], ws, is_member, parts))
    page, meta = slack_paginate(dtos, cursor=cursor, limit=limit)
    body = {"ok": True, "channels": page}
    body.update(meta)
    return JSONResponse(body)


@router.post("/api/conversations.history")
@router.get("/api/conversations.history")
async def history(request: Request):
    ws, err = await require_identity(request)
    if err:
        return err
    rl = await ratelimit_check("conversations.history", identity=ws["team_id"],
                               app_distribution=ws["app_distribution"])
    if rl is not None:
        return rl
    params = await read_params(request)
    channel = str_param(params, "channel")
    cursor = str_param(params, "cursor")
    if _bad_cursor(cursor):
        return JSONResponse(slack_error("invalid_cursor"))
    limit, _ = _history_limit(ws, params)
    oldest = str_param(params, "oldest")
    latest = str_param(params, "latest")
    inclusive = bool_param(params, "inclusive", False)
    if oldest is not None and not _TS_RE.match(oldest):
        return JSONResponse(slack_error("invalid_ts_oldest"))
    if latest is not None and not _TS_RE.match(latest):
        return JSONResponse(slack_error("invalid_ts_latest"))
    if not channel:
        return JSONResponse(slack_error("channel_not_found"))
    chan = await _find_channel(ws["id"], channel)
    if chan is None:
        return JSONResponse(slack_error("channel_not_found"))
    visible, is_member, _ = await _visibility(ws, chan)
    if not visible:
        return JSONResponse(slack_error("channel_not_found"))
    if not is_member:
        return JSONResponse(slack_error("not_in_channel"))

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
               m.reply_count, m.reply_users_count, m.latest_reply, m.reply_users,
               m.client_msg_id, m.reactions, m.edited,
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
    body = {"ok": True, "messages": page, "has_more": has_more, "pin_count": 0}
    if latest is not None:
        body["latest"] = latest
    body.update(meta)
    return JSONResponse(body)


@router.post("/api/conversations.replies")
@router.get("/api/conversations.replies")
async def replies(request: Request):
    ws, err = await require_identity(request)
    if err:
        return err
    rl = await ratelimit_check("conversations.replies", identity=ws["team_id"],
                               app_distribution=ws["app_distribution"])
    if rl is not None:
        return rl
    params = await read_params(request)
    channel = str_param(params, "channel")
    ts = str_param(params, "ts")
    cursor = str_param(params, "cursor")
    if _bad_cursor(cursor):
        return JSONResponse(slack_error("invalid_cursor"))
    limit, _ = _history_limit(ws, params)
    if not channel:
        return JSONResponse(slack_error("channel_not_found"))
    if not ts:
        return JSONResponse(slack_error("thread_not_found"))
    chan = await _find_channel(ws["id"], channel)
    if chan is None:
        return JSONResponse(slack_error("channel_not_found"))
    visible, is_member, _ = await _visibility(ws, chan)
    if not visible:
        return JSONResponse(slack_error("channel_not_found"))
    if not is_member:
        return JSONResponse(slack_error("not_in_channel"))

    # The thread parent must actually exist in this channel.
    parent = await state().pool.fetchval(
        "SELECT 1 FROM app_slack.messages WHERE channel_pk = $1 AND ts = $2 AND is_hidden = FALSE",
        chan["id"], ts,
    )
    if not parent:
        return JSONResponse(slack_error("thread_not_found"))

    sql = """
        SELECT m.ts, m.thread_ts, m.subtype, m.text, m.blocks, m.attachments,
               m.reply_count, m.reply_users_count, m.latest_reply, m.reply_users,
               m.client_msg_id, m.reactions, m.edited,
               u.slack_user_id AS user_id
          FROM app_slack.messages m
          LEFT JOIN app_slack.users u ON u.id = m.user_pk
         WHERE m.channel_pk = $1 AND (m.ts = $2 OR m.thread_ts = $2)
           AND m.is_hidden = FALSE
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
    ws, err = await require_identity(request)
    if err:
        return err
    rl = await ratelimit_check("conversations.members", identity=ws["team_id"])
    if rl is not None:
        return rl
    params = await read_params(request)
    channel = str_param(params, "channel")
    cursor = str_param(params, "cursor")
    if _bad_cursor(cursor):
        return JSONResponse(slack_error("invalid_cursor"))
    limit = int_param(params, "limit", 100, lo=1, hi=1000)
    if not channel:
        return JSONResponse(slack_error("channel_not_found"))
    chan = await _find_channel(ws["id"], channel)
    if chan is None:
        return JSONResponse(slack_error("channel_not_found"))
    visible, _is_member, parts = await _visibility(ws, chan)
    if not visible:
        return JSONResponse(slack_error("channel_not_found"))

    # Real Slack returns exactly that conversation's members — never the whole
    # workspace. Participants = membership ∪ message authors.
    if not parts:
        parts = await _participants(chan["id"])
    ids = sorted(parts.keys())
    page, meta = slack_paginate(ids, cursor=cursor, limit=limit)
    body = {"ok": True, "members": page}
    body.update(meta)
    return JSONResponse(body)


async def _member_count(channel_pk: UUID) -> int:
    return len(await _participants(channel_pk))
