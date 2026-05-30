"""GET /rest/api/3/myself — the authenticated account (connectivity probe)."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from spammers.jira.auth import authed_install
from spammers.jira.routes._common import unauthorized

router = APIRouter()


@router.get("/rest/api/3/myself")
async def myself(request: Request):
    inst = await authed_install(request)
    if inst is None:
        return unauthorized()
    base_url = inst["base_url"]
    return JSONResponse({
        "self": f"{base_url}/rest/api/3/user?accountId={inst['account_id']}",
        "accountId": inst["account_id"],
        "accountType": "atlassian",
        "emailAddress": inst["account_email"],
        "displayName": inst["account_email"].split("@", 1)[0].replace(".", " ").title(),
        "active": True,
        "timeZone": "Etc/UTC",
    })
