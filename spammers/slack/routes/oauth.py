"""Slack OAuth v2 — install authorize + token exchange (the two-token model).

Real flow:
  1. App redirects the user to
     ``https://slack.com/oauth/v2/authorize?client_id=&scope=&user_scope=&state=&redirect_uri=``
     ``scope``      = requested **bot** scopes  → an ``xoxb`` token.
     ``user_scope`` = requested **user** scopes → an ``xoxp`` token acting AS the
                      consenting human (this is what Fyralis uses to read DMs).
  2. User approves; Slack 302s back to ``redirect_uri?code=&state=``.
  3. App POSTs ``oauth.v2.access`` with ``code`` + client creds and receives:
       - top-level ``access_token`` = the **bot** token (xoxb),
       - ``authed_user.access_token`` = the **user** token (xoxp) — but ONLY when
         ``user_scope`` was requested/granted. Its ``id`` is the consenting human
         and its ``scope`` is exactly the granted user scopes.

The mock mirrors this. Because the mock authorize page has no logged-in Slack
session, the consenting human is identified by an optional ``user_id`` query
param (mock convenience); absent that we pick a deterministic workspace user.
"""
from __future__ import annotations

import base64
import json
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from spammers.common.errors import slack_error
from spammers.common.ids import oauth_code, slack_user_token
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
    user_id: str = "",
):
    """Mock approve page → meta-refresh redirect to ``redirect_uri?code=&state=``.

    Captures the requested ``scope`` / ``user_scope`` and the consenting human so
    the exchange can mint a correctly-scoped bot token and (when user scopes were
    requested) a distinct xoxp user token.
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
        # Resolve the consenting human. Prefer an explicit user_id; else pick a
        # deterministic workspace user (a real install would be the logged-in user).
        authed_user_id = None
        if user_id:
            urow = await conn.fetchrow(
                "SELECT slack_user_id FROM app_slack.users "
                "WHERE workspace_id = $1 AND slack_user_id = $2",
                row["id"], user_id,
            )
            authed_user_id = urow["slack_user_id"] if urow else None
        if authed_user_id is None:
            urow = await conn.fetchrow(
                "SELECT slack_user_id FROM app_slack.users "
                "WHERE workspace_id = $1 AND is_bot = FALSE "
                "ORDER BY slack_user_id LIMIT 1",
                row["id"],
            )
            authed_user_id = urow["slack_user_id"] if urow else None

        await conn.execute(
            """
            INSERT INTO oauth.codes
                (id, run_id, provider, code, redirect_uri, state,
                 scope, user_scope, authed_user_id)
            VALUES ($1, $2, 'slack', $3, $4, $5, $6, $7, $8)
            """,
            uuid4(), st.run_id, code, redirect_uri, state,
            scope or "", user_scope or "", authed_user_id,
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
    """Exchange ``code`` for a bot token (+ a user token when user_scope granted).

    Accepts ``application/x-www-form-urlencoded`` (the documented default), JSON,
    and ``Authorization: Basic`` client credentials.
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
            # Real Slack: unknown client_id → invalid_client_id; known client_id
            # with a wrong secret → bad_client_secret (distinct errors).
            if ws is None:
                return JSONResponse(slack_error("invalid_client_id"))
            if ws["client_secret"] != client_secret:
                return JSONResponse(slack_error("bad_client_secret"))

            row = await conn.fetchrow(
                """
                UPDATE oauth.codes
                   SET consumed_at = now()
                 WHERE run_id = $1
                   AND provider = 'slack'
                   AND code = $2
                   AND consumed_at IS NULL
                   AND expires_at > now()
                 RETURNING id, redirect_uri, state, scope, user_scope, authed_user_id
                """,
                st.run_id, code,
            )
            if row is None:
                return JSONResponse(slack_error("invalid_code"))

            bot_scope = row["scope"] or _DEFAULT_BOT_SCOPE
            user_scope = (row["user_scope"] or "").strip()
            authed_user_id = row["authed_user_id"] or ws["bot_user_id"]

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
                      scopes      = EXCLUDED.scopes,
                      revoked_at  = NULL
                """,
                uuid4(), st.run_id, ws["team_id"],
                ws["bot_token"], ws["bot_token"],
                json.dumps([s for s in bot_scope.split(",") if s]),
                json.dumps({"app_id": ws["app_id"], "bot_user_id": ws["bot_user_id"]}),
            )

            # Mint a distinct xoxp user token ONLY when user scopes were granted.
            user_token = None
            if user_scope and authed_user_id:
                user_token = slack_user_token()
                await conn.execute(
                    """
                    INSERT INTO app_slack.user_tokens
                        (id, workspace_id, slack_user_id, user_token, scopes)
                    VALUES ($1, $2, $3, $4, $5::jsonb)
                    ON CONFLICT (workspace_id, slack_user_id) DO UPDATE
                      SET user_token = EXCLUDED.user_token,
                          scopes     = EXCLUDED.scopes,
                          revoked_at = NULL
                    """,
                    uuid4(), ws["id"], authed_user_id, user_token,
                    json.dumps([s for s in user_scope.split(",") if s]),
                )

    resp = {
        "ok": True,
        "access_token": ws["bot_token"],
        "token_type": "bot",
        "scope": bot_scope,
        "bot_user_id": ws["bot_user_id"],
        "app_id": ws["app_id"],
        "team": {"id": ws["team_id"], "name": ws["team_name"]},
        "enterprise": None,
        "is_enterprise_install": bool(ws["enterprise_id"]),
        # authed_user.access_token/scope/token_type appear only when user scopes
        # were granted; otherwise just the consenting user's id is returned.
        "authed_user": {"id": authed_user_id},
    }
    if user_token:
        resp["authed_user"].update({
            "scope": user_scope,
            "access_token": user_token,
            "token_type": "user",
        })
    if ws["enterprise_id"]:
        resp["enterprise"] = {"id": ws["enterprise_id"], "name": ws["enterprise_name"]}
    return JSONResponse(resp)


_DEFAULT_BOT_SCOPE = (
    "channels:read,channels:history,groups:read,groups:history,"
    "users:read,users:read.email,team:read,chat:write"
)


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
