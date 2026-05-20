"""team.info — workspace metadata."""
from __future__ import annotations

from fastapi import APIRouter, Request

from spammers.common.errors import slack_error
from spammers.slack.auth import resolve_workspace
from spammers.slack.ratelimit import check as ratelimit_check
from spammers.slack.responses import SlackJSONResponse as JSONResponse


router = APIRouter()


@router.post("/api/team.info")
@router.get("/api/team.info")
async def team_info(request: Request):
    ws = await resolve_workspace(request)
    if ws is None:
        return JSONResponse(slack_error("invalid_auth"))
    rl = await ratelimit_check("team.info", identity=ws["team_id"])
    if rl is not None:
        return rl
    return JSONResponse({
        "ok": True,
        "team": {
            "id": ws["team_id"],
            "name": ws["team_name"],
            "domain": ws["team_domain"],
            "email_domain": "spammer-org.test",
            "icon": {
                "image_default": True,
                "image_34":  "https://avatars.spammer.test/team/34.png",
                "image_44":  "https://avatars.spammer.test/team/44.png",
                "image_68":  "https://avatars.spammer.test/team/68.png",
                "image_88":  "https://avatars.spammer.test/team/88.png",
                "image_102": "https://avatars.spammer.test/team/102.png",
                "image_132": "https://avatars.spammer.test/team/132.png",
            },
        },
    })
