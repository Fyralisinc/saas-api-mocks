"""auth.test — verify token, return identity."""
from __future__ import annotations

from fastapi import APIRouter, Request

from spammers.common.errors import slack_error
from spammers.slack.auth import resolve_workspace
from spammers.slack.ratelimit import check as ratelimit_check
from spammers.slack.responses import SlackJSONResponse as JSONResponse


router = APIRouter()


@router.post("/api/auth.test")
@router.get("/api/auth.test")
async def auth_test(request: Request):
    rl = await ratelimit_check("auth.test", identity=request.client.host if request.client else "anon")
    if rl is not None:
        return rl
    ws = await resolve_workspace(request)
    if ws is None:
        return JSONResponse(slack_error("invalid_auth"))
    return JSONResponse({
        "ok": True,
        "url": f"https://{ws['team_domain']}.slack.com/",
        "team": ws["team_name"],
        "user": "fyralis-bot",
        "team_id": ws["team_id"],
        "user_id": ws["bot_user_id"],
        "bot_id": ws["bot_id"],
    })
