"""auth.test — verify token, return identity.

Reflects the token TYPE: a bot token returns the bot user + a B-prefixed
``bot_id``; a user token returns the acting human's ``user_id`` and OMITS
``bot_id`` (real Slack only includes bot_id for bot-user tokens).
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from spammers.slack.auth import require_identity
from spammers.slack.ratelimit import check as ratelimit_check
from spammers.slack.responses import SlackJSONResponse as JSONResponse
from spammers.slack.state import state


router = APIRouter()


@router.post("/api/auth.test")
@router.get("/api/auth.test")
async def auth_test(request: Request):
    # auth.test needs a valid token before it can be attributed to a workspace
    # bucket — resolve first, then rate-check on the resolved workspace.
    ws, err = await require_identity(request)
    if err:
        return err
    rl = await ratelimit_check("auth.test", identity=ws["team_id"])
    if rl is not None:
        return rl

    resp = {
        "ok": True,
        "url": f"https://{ws['team_domain']}.slack.com/",
        "team": ws["team_name"],
        "team_id": ws["team_id"],
    }
    if ws["token_type"] == "user":
        handle = await state().pool.fetchval(
            "SELECT p.handle FROM app_slack.users u JOIN org.people p ON p.id = u.person_id "
            "WHERE u.workspace_id = $1 AND u.slack_user_id = $2",
            ws["id"], ws["acting_user_id"],
        )
        resp["user"] = handle or ws["acting_user_id"]
        resp["user_id"] = ws["acting_user_id"]
    else:
        handle = await state().pool.fetchval(
            "SELECT p.handle FROM app_slack.users u JOIN org.people p ON p.id = u.person_id "
            "WHERE u.workspace_id = $1 AND u.slack_user_id = $2",
            ws["id"], ws["bot_user_id"],
        )
        resp["user"] = handle or "fyralis-bot"
        resp["user_id"] = ws["bot_user_id"]
        resp["bot_id"] = ws["bot_id"]
    if ws.get("enterprise_id"):
        resp["is_enterprise_install"] = True
        resp["enterprise_id"] = ws["enterprise_id"]
    return JSONResponse(resp)
