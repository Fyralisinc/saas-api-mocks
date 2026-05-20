"""Slack OAuth v2 — install authorize + token exchange.

Real flow:
  1. App redirects user to
     ``https://slack.com/oauth/v2/authorize?client_id=&scope=&user_scope=&state=&redirect_uri=``
  2. User approves; Slack 302s back to ``redirect_uri?code=&state=``
  3. App calls
     ``POST https://slack.com/api/oauth.v2.access``
     with ``code`` and ``client_id`` + ``client_secret``, gets the bot token.

The mock implements both endpoints with the response schema Slack returns.
"""
from __future__ import annotations

import base64
import json
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from spammers.common.errors import slack_error
from spammers.common.ids import oauth_code
from spammers.slack.responses import SlackJSONResponse as JSONResponse
from spammers.slack.state import state as get_state


router = APIRouter()


@router.get("/oauth/v2/authorize")
async def authorize(
    request: Request,
    client_id: str,
    redirect_uri: str,
    state: str = "",
    scope: str = "",
    user_scope: str = "",
):
    """Mock approve page.

    Returns an HTML page with a meta-refresh that redirects to
    ``redirect_uri?code=&state=``. The Director can also POST to
    ``/oauth/v2/approve`` to skip the HTML hop.
    """
    code = oauth_code()
    st = get_state()
    async with st.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, team_id FROM app_slack.workspaces WHERE run_id = $1 AND client_id = $2",
            st.run_id, client_id,
        )
        if row is None:
            # Real Slack renders a user-facing HTML page (HTTP 200) for a bad
            # client_id on the authorize endpoint — not a bare JSON 4xx.
            return HTMLResponse(
                "<!doctype html><html><body><h1>Oops!</h1>"
                "<p>That app couldn't be installed: invalid client_id.</p>"
                "</body></html>",
                status_code=200,
            )
        await conn.execute(
            """
            INSERT INTO oauth.codes(id, run_id, provider, code, redirect_uri, state)
            VALUES ($1, $2, 'slack', $3, $4, $5)
            """,
            uuid4(), st.run_id, code, redirect_uri, state,
        )
    redirect_to = f"{redirect_uri}?{urlencode({'code': code, 'state': state})}"
    html = f"""<!doctype html>
<html><head><title>Approve install (mock)</title>
<meta http-equiv="refresh" content="0; url={redirect_to}">
</head><body>
<h1>Slack mock — auto-approving install</h1>
<p>Redirecting to <a href="{redirect_to}">{redirect_to}</a>.</p>
</body></html>
"""
    return HTMLResponse(html)


@router.post("/api/oauth.v2.access")
async def access(request: Request):
    """Exchange ``code`` for a bot token + workspace metadata.

    Real Slack accepts both ``application/x-www-form-urlencoded`` (the
    documented default) and JSON bodies, plus ``Authorization: Basic``
    for client credentials. We mirror all three.
    """
    st = get_state()
    fields = await _parse_body(request)
    code = fields.get("code", "")
    client_id = fields.get("client_id", "")
    client_secret = fields.get("client_secret", "")

    if not client_id:
        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(auth.split(None, 1)[1]).decode("utf-8")
                if ":" in decoded:
                    client_id, client_secret = decoded.split(":", 1)
            except Exception:
                pass

    async with st.pool.acquire() as conn:
        async with conn.transaction():
            ws = await conn.fetchrow(
                """
                SELECT id, team_id, team_name, team_domain, client_id, client_secret,
                       bot_token, bot_user_id, app_id, enterprise_id, enterprise_name
                  FROM app_slack.workspaces
                 WHERE run_id = $1 AND client_id = $2
                """,
                st.run_id, client_id,
            )
            if ws is None or ws["client_secret"] != client_secret:
                return JSONResponse(slack_error("invalid_client_id"))

            row = await conn.fetchrow(
                """
                UPDATE oauth.codes
                   SET consumed_at = now()
                 WHERE run_id = $1
                   AND provider = 'slack'
                   AND code = $2
                   AND consumed_at IS NULL
                   AND expires_at > now()
                 RETURNING id, redirect_uri, state
                """,
                st.run_id, code,
            )
            if row is None:
                return JSONResponse(slack_error("invalid_code"))

            await conn.execute(
                """
                INSERT INTO oauth.installs
                    (id, run_id, provider, fyralis_tenant_id, provider_account_id,
                     access_token, bot_token, scopes, extra)
                VALUES ($1, $2, 'slack',
                        (SELECT fyralis_tenant_id FROM org.runs WHERE id = $2),
                        $3, $4, $5, $6::jsonb, $7::jsonb)
                ON CONFLICT (run_id, provider, provider_account_id) DO UPDATE
                  SET access_token = EXCLUDED.access_token,
                      bot_token   = EXCLUDED.bot_token,
                      revoked_at  = NULL
                """,
                uuid4(), st.run_id, ws["team_id"],
                ws["bot_token"], ws["bot_token"],
                json.dumps(["chat:write", "users:read", "channels:history", "channels:read",
                            "groups:history", "groups:read", "team:read", "users:read.email"]),
                json.dumps({"app_id": ws["app_id"], "bot_user_id": ws["bot_user_id"]}),
            )

    resp = {
        "ok": True,
        "access_token": ws["bot_token"],
        "token_type": "bot",
        "scope": "chat:write,users:read,channels:history,channels:read,groups:history,groups:read,team:read",
        "bot_user_id": ws["bot_user_id"],
        "app_id": ws["app_id"],
        "team": {"id": ws["team_id"], "name": ws["team_name"]},
        "enterprise": None,
        "authed_user": {
            "id": ws["bot_user_id"],
            "scope": "identify",
            "access_token": ws["bot_token"],
            "token_type": "user",
        },
    }
    if ws["enterprise_id"]:
        resp["enterprise"] = {"id": ws["enterprise_id"], "name": ws["enterprise_name"]}
    return JSONResponse(resp)


async def _parse_body(request: Request) -> dict[str, str]:
    ctype = (request.headers.get("content-type") or "").lower()
    body = await request.body()
    if ctype.startswith("application/json"):
        try:
            data = json.loads(body or b"{}")
            return {k: str(v) for k, v in data.items() if isinstance(v, (str, int, float))}
        except Exception:
            return {}
    # default to x-www-form-urlencoded
    from urllib.parse import parse_qsl
    return dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True))
