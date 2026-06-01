"""users.info / users.list — user profiles + identity.

Both endpoints return the SAME full user object shape (real Slack does). The
``email`` field is gated on the ``users:read.email`` scope, ``tz_offset`` is the
real integer seconds offset, and ``updated`` is a Unix timestamp.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request

from spammers.common.errors import slack_error
from spammers.common.pagination import slack_paginate
from spammers.slack.params import int_param, read_params, str_param
from spammers.slack.responses import SlackJSONResponse as JSONResponse
from spammers.slack.auth import has_scope, require_identity
from spammers.slack.ratelimit import check as ratelimit_check
from spammers.slack.state import state


router = APIRouter()


def _tz_fields(tz_name: Optional[str]) -> tuple[str, str, int]:
    """(tz, tz_label, tz_offset_seconds) for a zoneinfo tz name."""
    tz_name = tz_name or "America/Los_Angeles"
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(tz_name))
        offset = int(now.utcoffset().total_seconds()) if now.utcoffset() else 0
        label = now.tzname() or tz_name
    except Exception:
        offset, label = 0, tz_name
    return tz_name, label, offset


def serialize_user(row: dict, team_id: str, *, include_email: bool) -> dict:
    """Full user object — identical shape from users.info and users.list."""
    profile_extra = row["profile"] if isinstance(row["profile"], dict) else json.loads(row["profile"] or "{}")
    handle = row["handle"]
    tz, tz_label, tz_offset = _tz_fields(row.get("timezone"))
    updated = int(row["started_at"].timestamp()) if row.get("started_at") else 0

    profile = {
        "title": profile_extra.get("title", ""),
        "real_name": row["full_name"],
        "real_name_normalized": row["full_name"],
        "display_name": handle,
        "display_name_normalized": handle.lower(),
        "status_text": "",
        "status_emoji": "",
        "avatar_hash": "g" + handle[:8],
        "image_24":  f"https://avatars.spammer.test/{handle}/24.png",
        "image_32":  f"https://avatars.spammer.test/{handle}/32.png",
        "image_48":  f"https://avatars.spammer.test/{handle}/48.png",
        "image_72":  f"https://avatars.spammer.test/{handle}/72.png",
        "image_192": f"https://avatars.spammer.test/{handle}/192.png",
        "image_512": f"https://avatars.spammer.test/{handle}/512.png",
        "team": team_id,
    }
    if include_email:
        profile["email"] = row["email"]

    return {
        "id": row["id"],
        "team_id": team_id,
        "name": handle,
        "deleted": row["deleted"],
        "color": "9f69e7",
        "real_name": row["full_name"],
        "tz": tz,
        "tz_label": tz_label,
        "tz_offset": tz_offset,
        "profile": profile,
        "is_admin": False,
        "is_owner": False,
        "is_primary_owner": False,
        "is_restricted": False,
        "is_ultra_restricted": False,
        "is_bot": row["is_bot"],
        "is_app_user": False,
        "updated": updated,
        "has_2fa": False,
    }


@router.post("/api/users.info")
@router.get("/api/users.info")
async def users_info(request: Request):
    ws, err = await require_identity(request)
    if err:
        return err
    rl = await ratelimit_check("users.info", identity=ws["team_id"])
    if rl is not None:
        return rl

    params = await read_params(request)
    user = str_param(params, "user")
    if not user:
        return JSONResponse(slack_error("user_not_found"))

    row = await state().pool.fetchrow(
        """
        SELECT u.slack_user_id AS id, u.is_bot, u.deleted, u.profile,
               p.full_name, p.handle, p.email, p.timezone, p.started_at
          FROM app_slack.users u
          JOIN org.people p ON p.id = u.person_id
         WHERE u.workspace_id = $1 AND u.slack_user_id = $2
        """,
        ws["id"], user,
    )
    if row is None:
        return JSONResponse(slack_error("user_not_found"))
    return JSONResponse({
        "ok": True,
        "user": serialize_user(dict(row), ws["team_id"],
                               include_email=has_scope(ws, "users:read.email")),
    })


@router.post("/api/users.list")
@router.get("/api/users.list")
async def users_list(request: Request):
    ws, err = await require_identity(request)
    if err:
        return err
    rl = await ratelimit_check("users.list", identity=ws["team_id"])
    if rl is not None:
        return rl

    params = await read_params(request)
    cursor = str_param(params, "cursor")
    limit = int_param(params, "limit", 100, lo=1, hi=1000)
    rows = await state().pool.fetch(
        """
        SELECT u.slack_user_id AS id, u.is_bot, u.deleted, u.profile,
               p.full_name, p.handle, p.email, p.timezone, p.started_at
          FROM app_slack.users u
          JOIN org.people p ON p.id = u.person_id
         WHERE u.workspace_id = $1
         ORDER BY p.handle ASC
        """,
        ws["id"],
    )
    include_email = has_scope(ws, "users:read.email")
    members = [serialize_user(dict(r), ws["team_id"], include_email=include_email) for r in rows]
    page, meta = slack_paginate(members, cursor=cursor, limit=limit)
    body = {"ok": True, "members": page, "cache_ts": int(datetime.now(timezone.utc).timestamp())}
    body.update(meta)
    return JSONResponse(body)
