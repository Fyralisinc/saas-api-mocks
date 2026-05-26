"""Notion OAuth: GET /v1/oauth/authorize (auto-approve) + POST /v1/oauth/token.

Primary auth for the consumer is the internal-integration bot token (Bearer),
but the public-integration install flow is supported too: authorize redirects
straight back with a code, and the token exchange hands out the run's bot token.
"""
from __future__ import annotations

import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from spammers.notion.responses import NotionJSONResponse as JSONResponse
from spammers.notion.state import state

router = APIRouter()


@router.get("/v1/oauth/authorize")
async def authorize(request: Request):
    qs = request.query_params
    redirect_uri = qs.get("redirect_uri")
    st = qs.get("state", "")
    if not redirect_uri:
        return JSONResponse({"error": "invalid_request", "error_description": "redirect_uri required"},
                            status_code=400)
    code = secrets.token_urlsafe(24)
    return RedirectResponse(url=f"{redirect_uri}?{urlencode({'code': code, 'state': st})}", status_code=302)


@router.post("/v1/oauth/token")
async def token(request: Request):
    st = state()
    return JSONResponse({
        "access_token": st.bot_token,
        "token_type": "bearer",
        "bot_id": st.bot_user_id,
        "workspace_id": st.workspace_id,
        "workspace_name": st.workspace_name,
        "workspace_icon": None,
        "owner": {"type": "workspace", "workspace": True},
        "duplicated_template_id": None,
    })
