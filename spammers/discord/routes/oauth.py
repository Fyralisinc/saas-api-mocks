"""Discord OAuth2 — install authorize + token exchange.

Real flow:
  1. App redirects user to ``https://discord.com/oauth2/authorize?client_id=&
     scope=bot+applications.commands&permissions=&redirect_uri=&state=&response_type=code``
  2. User approves; Discord 302s back to ``redirect_uri?code=&state=&guild_id=``
  3. App calls ``POST /api/v10/oauth2/token`` (form-encoded) with ``grant_type=
     authorization_code``, ``code``, ``redirect_uri``, ``client_id`` +
     ``client_secret`` and gets an access token.

The mock implements both with Discord's response schema.
"""
from __future__ import annotations

import base64
import json
from urllib.parse import parse_qsl, urlencode
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from spammers.common.errors import discord_error
from spammers.common.ids import oauth_code
from spammers.discord.dto import application_dto
from spammers.discord.responses import DiscordJSONResponse
from spammers.discord.routes._deps import authed
from spammers.discord.state import state as get_state

router = APIRouter()

DEFAULT_SCOPE = "bot applications.commands"
TOKEN_TTL_S = 604800  # Discord access tokens live 7 days.


@router.get("/api/v10/oauth2/applications/@me")
async def get_application_me(request: Request):
    """The bot's own application — discord.py's login()/setup fetches this."""
    app, headers, err = await authed(request, "oauth2/applications/@me")
    if err is not None:
        return err
    return DiscordJSONResponse(application_dto(app), headers=headers)


@router.get("/oauth2/authorize")
async def authorize(
    request: Request,
    client_id: str,
    redirect_uri: str = "",
    state: str = "",
    scope: str = "",
    response_type: str = "code",
    permissions: str = "",
):
    """Auto-approving consent page — meta-refreshes to ``redirect_uri?code=&state=&guild_id=``."""
    st = get_state()
    async with st.pool.acquire() as conn:
        app = await conn.fetchrow(
            """
            SELECT a.id, a.client_id, g.guild_id
              FROM app_discord.applications a
              LEFT JOIN app_discord.guilds g ON g.application_pk = a.id
             WHERE a.run_id = $1 AND a.client_id = $2
             LIMIT 1
            """,
            st.run_id, client_id,
        )
        if app is None:
            return HTMLResponse(
                "<!doctype html><html><body><h1>Unauthorized</h1>"
                "<p>Invalid OAuth2 client_id.</p></body></html>",
                status_code=200,
            )
        code = oauth_code()
        await conn.execute(
            "INSERT INTO oauth.codes(id, run_id, provider, code, redirect_uri, state) "
            "VALUES ($1, $2, 'discord', $3, $4, $5)",
            uuid4(), st.run_id, code, redirect_uri, state,
        )
    params = {"code": code, "state": state}
    if app["guild_id"]:
        params["guild_id"] = app["guild_id"]
    redirect_to = f"{redirect_uri}?{urlencode(params)}"
    html = f"""<!doctype html>
<html><head><title>Authorize (mock)</title>
<meta http-equiv="refresh" content="0; url={redirect_to}">
</head><body>
<h1>Discord mock — auto-approving authorization</h1>
<p>Redirecting to <a href="{redirect_to}">{redirect_to}</a>.</p>
</body></html>
"""
    return HTMLResponse(html)


@router.post("/api/v10/oauth2/token")
async def token(request: Request):
    """Exchange ``code`` for an access token (form-encoded, like real Discord)."""
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
            app = await conn.fetchrow(
                """
                SELECT a.id, a.application_id, a.client_id, a.client_secret, a.bot_token,
                       g.guild_id
                  FROM app_discord.applications a
                  LEFT JOIN app_discord.guilds g ON g.application_pk = a.id
                 WHERE a.run_id = $1 AND a.client_id = $2
                 LIMIT 1
                """,
                st.run_id, client_id,
            )
            if app is None or app["client_secret"] != client_secret:
                return DiscordJSONResponse(
                    {"error": "invalid_client"}, status_code=401,
                )
            row = await conn.fetchrow(
                """
                UPDATE oauth.codes SET consumed_at = now()
                 WHERE run_id = $1 AND provider = 'discord' AND code = $2
                   AND consumed_at IS NULL AND expires_at > now()
                 RETURNING id
                """,
                st.run_id, code,
            )
            if row is None:
                return DiscordJSONResponse({"error": "invalid_grant"}, status_code=400)

            await conn.execute(
                """
                INSERT INTO oauth.installs
                    (id, run_id, provider, fyralis_tenant_id, provider_account_id,
                     access_token, bot_token, expires_at, scopes, extra)
                VALUES ($1, $2, 'discord',
                        (SELECT fyralis_tenant_id FROM org.runs WHERE id = $2),
                        $3, $4, $5, now() + ($6 || ' seconds')::interval, $7::jsonb, $8::jsonb)
                ON CONFLICT (run_id, provider, provider_account_id) DO UPDATE
                  SET access_token = EXCLUDED.access_token,
                      bot_token   = EXCLUDED.bot_token,
                      expires_at  = EXCLUDED.expires_at,
                      revoked_at  = NULL
                """,
                uuid4(), st.run_id, app["guild_id"] or app["application_id"],
                app["bot_token"], app["bot_token"], str(TOKEN_TTL_S),
                json.dumps(DEFAULT_SCOPE.split()),
                json.dumps({"application_id": app["application_id"]}),
            )

    return DiscordJSONResponse({
        "access_token": app["bot_token"],
        "token_type": "Bearer",
        "expires_in": TOKEN_TTL_S,
        "refresh_token": oauth_code(),
        "scope": DEFAULT_SCOPE,
    })


async def _parse_body(request: Request) -> dict[str, str]:
    ctype = (request.headers.get("content-type") or "").lower()
    body = await request.body()
    if ctype.startswith("application/json"):
        try:
            data = json.loads(body or b"{}")
            return {k: str(v) for k, v in data.items() if isinstance(v, (str, int, float))}
        except Exception:
            return {}
    return dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True))
