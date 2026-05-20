"""Bot-token resolution for inbound Slack Web API calls.

Real Slack honors ``Authorization: Bearer xoxb-…`` (and also
``token=`` in form bodies for legacy methods). We support both.

On a missing/invalid token, return Slack's
``{"ok": false, "error": "invalid_auth" | "not_authed" | "token_revoked"}``.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Request

from spammers.slack.state import state


async def resolve_workspace(request: Request) -> Optional[dict]:
    """Resolve the calling workspace from the bot token. Returns the
    workspace row (or None on invalid auth).

    Looked up against ``app_slack.workspaces`` AND ``oauth.installs`` (since
    Slack issues a token on install; we use the latter as the live-token
    canonical source).
    """
    token = _extract_token(request)
    if not token:
        return None
    st = state()
    row = await st.pool.fetchrow(
        """
        SELECT w.id, w.team_id, w.team_name, w.team_domain, w.bot_user_id,
               w.signing_secret, w.app_id
          FROM app_slack.workspaces w
          JOIN oauth.installs i
            ON i.run_id = w.run_id
           AND i.provider = 'slack'
           AND i.provider_account_id = w.team_id
         WHERE w.run_id = $1
           AND i.bot_token = $2
           AND i.revoked_at IS NULL
         LIMIT 1
        """,
        st.run_id, token,
    )
    if row is None:
        # Fallback: token may be the workspace.bot_token directly (pre-install seed)
        row = await st.pool.fetchrow(
            "SELECT id, team_id, team_name, team_domain, bot_user_id, signing_secret, app_id "
            "FROM app_slack.workspaces WHERE run_id = $1 AND bot_token = $2",
            st.run_id, token,
        )
    if row is None:
        return None
    ws = dict(row)
    ws["bot_id"] = _bot_id(ws["bot_user_id"])
    return ws


def _bot_id(bot_user_id: str) -> str:
    """Real Slack bot ids use a ``B`` prefix, distinct from the bot's ``U`` user id."""
    return "B" + bot_user_id[1:] if bot_user_id.startswith("U") else bot_user_id


def _extract_token(request: Request) -> Optional[str]:
    h = request.headers.get("authorization") or request.headers.get("Authorization")
    if h:
        h = h.strip()
        if h.lower().startswith("bearer "):
            return h[7:].strip()
        return h
    # form-body token=
    # (we don't parse body here; FastAPI routes will pass it explicitly if needed)
    return None
