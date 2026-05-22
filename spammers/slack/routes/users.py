"""users.info — return a user's profile + identity."""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Request

from spammers.common.errors import slack_error
from spammers.common.pagination import slack_paginate
from spammers.slack.params import int_param, read_params, str_param
from spammers.slack.responses import SlackJSONResponse as JSONResponse
from spammers.slack.auth import resolve_workspace
from spammers.slack.ratelimit import check as ratelimit_check
from spammers.slack.state import state


router = APIRouter()


@router.post("/api/users.info")
@router.get("/api/users.info")
async def users_info(request: Request):
    ws = await resolve_workspace(request)
    if ws is None:
        return JSONResponse(slack_error("invalid_auth"))
    rl = await ratelimit_check("users.info", identity=ws["team_id"])
    if rl is not None:
        return rl

    params = await read_params(request)
    user = str_param(params, "user")
    if not user:
        return JSONResponse(slack_error("user_not_found"))

    st = state()
    row = await st.pool.fetchrow(
        """
        SELECT u.slack_user_id AS id, u.is_bot, u.deleted, u.profile,
               p.full_name, p.handle, p.email, p.timezone
          FROM app_slack.users u
          JOIN org.people p ON p.id = u.person_id
         WHERE u.workspace_id = $1 AND u.slack_user_id = $2
        """,
        ws["id"], user,
    )
    if row is None:
        return JSONResponse(slack_error("user_not_found"))

    profile = row["profile"] if isinstance(row["profile"], dict) else json.loads(row["profile"] or "{}")
    return JSONResponse({
        "ok": True,
        "user": {
            "id": row["id"],
            "team_id": ws["team_id"],
            "name": row["handle"],
            "deleted": row["deleted"],
            "real_name": row["full_name"],
            "tz": row["timezone"],
            "tz_label": row["timezone"],
            "tz_offset": 0,
            "is_bot": row["is_bot"],
            "is_admin": False,
            "is_owner": False,
            "profile": {
                "real_name": row["full_name"],
                "display_name": row["handle"],
                "display_name_normalized": row["handle"].lower(),
                "email": row["email"],
                "title": profile.get("title", ""),
                "image_24":  f"https://avatars.spammer.test/{row['handle']}/24.png",
                "image_32":  f"https://avatars.spammer.test/{row['handle']}/32.png",
                "image_48":  f"https://avatars.spammer.test/{row['handle']}/48.png",
                "image_72":  f"https://avatars.spammer.test/{row['handle']}/72.png",
                "image_192": f"https://avatars.spammer.test/{row['handle']}/192.png",
                "image_512": f"https://avatars.spammer.test/{row['handle']}/512.png",
            },
            "updated": 0,
        },
    })


@router.post("/api/users.list")
@router.get("/api/users.list")
async def users_list(request: Request):
    ws = await resolve_workspace(request)
    if ws is None:
        return JSONResponse(slack_error("invalid_auth"))
    rl = await ratelimit_check("users.list", identity=ws["team_id"])
    if rl is not None:
        return rl

    params = await read_params(request)
    cursor = str_param(params, "cursor")
    limit = int_param(params, "limit", 100, lo=1, hi=1000)
    st = state()
    rows = await st.pool.fetch(
        """
        SELECT u.slack_user_id AS id, u.is_bot, u.deleted, u.profile,
               p.full_name, p.handle, p.email, p.timezone
          FROM app_slack.users u
          JOIN org.people p ON p.id = u.person_id
         WHERE u.workspace_id = $1
         ORDER BY p.handle ASC
        """,
        ws["id"],
    )
    members = []
    for row in rows:
        profile = row["profile"] if isinstance(row["profile"], dict) else json.loads(row["profile"] or "{}")
        members.append({
            "id": row["id"],
            "team_id": ws["team_id"],
            "name": row["handle"],
            "deleted": row["deleted"],
            "real_name": row["full_name"],
            "tz": row["timezone"],
            "is_bot": row["is_bot"],
            "profile": {
                "real_name": row["full_name"],
                "display_name": row["handle"],
                "email": row["email"],
                "title": profile.get("title", ""),
            },
        })

    page, meta = slack_paginate(members, cursor=cursor, limit=limit)
    body = {"ok": True, "members": page, "cache_ts": 0}
    body.update(meta)
    return JSONResponse(body)
