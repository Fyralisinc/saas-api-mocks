"""team.info — workspace metadata."""
from __future__ import annotations

from fastapi import APIRouter, Request

from spammers.slack.auth import require_identity
from spammers.slack.ratelimit import check as ratelimit_check
from spammers.slack.responses import SlackJSONResponse as JSONResponse


router = APIRouter()


@router.post("/api/team.info")
@router.get("/api/team.info")
async def team_info(request: Request):
    ws, err = await require_identity(request)
    if err:
        return err
    rl = await ratelimit_check("team.info", identity=ws["team_id"])
    if rl is not None:
        return rl
    team = {
        "id": ws["team_id"],
        "name": ws["team_name"],
        "domain": ws["team_domain"],
        "email_domain": f"{ws['team_domain']}.com",
        "icon": {
            "image_default": True,
            "image_34":  "https://avatars.spammer.test/team/34.png",
            "image_44":  "https://avatars.spammer.test/team/44.png",
            "image_68":  "https://avatars.spammer.test/team/68.png",
            "image_88":  "https://avatars.spammer.test/team/88.png",
            "image_102": "https://avatars.spammer.test/team/102.png",
            "image_132": "https://avatars.spammer.test/team/132.png",
        },
    }
    if ws.get("enterprise_id"):
        team["enterprise_id"] = ws["enterprise_id"]
        team["enterprise_name"] = ws["enterprise_name"]
    return JSONResponse({"ok": True, "team": team})
