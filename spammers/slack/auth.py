"""Token resolution for inbound Slack Web API calls — the two-token model.

Real Slack uses two token types, and they authenticate as different principals:

  * **bot token** (``xoxb-…``) authenticates AS the app's bot user. It reads
    public/private channels the bot is a member of. It **cannot** read
    human-human DMs.
  * **user token** (``xoxp-…``) authenticates AS the granting human. It reads
    that user's own 1:1 DMs (``im``) and group DMs (``mpim``).

So resolution must yield not just "which workspace" but "which principal, what
kind of token, and what scopes" — every DM-reading method depends on it.

We honour ``Authorization: Bearer <token>`` (the SDK default) and a ``?token=``
query param (legacy). On a missing token the caller should return Slack's
``not_authed``; on a present-but-unknown token, ``invalid_auth``.
"""
from __future__ import annotations

import json
from typing import Optional, Tuple

from fastapi import Request

from spammers.common.errors import slack_error
from spammers.common.ids import slack_bot_id
from spammers.slack.responses import SlackJSONResponse as JSONResponse
from spammers.slack.state import state


# Default bot scopes for a pre-install seeded bot_token (no oauth.installs row).
_DEFAULT_BOT_SCOPES = [
    "channels:read", "channels:history", "groups:read", "groups:history",
    "im:read", "im:history", "mpim:read", "mpim:history",
    "users:read", "users:read.email", "team:read", "chat:write",
]


async def resolve_identity(request: Request) -> Optional[dict]:
    """Resolve the calling principal from its token.

    Returns an identity dict, or ``None`` if the token is missing/unknown. The
    dict always carries the workspace fields plus:
        token_type     : "bot" | "user"
        token          : the raw token string
        scopes         : list[str] of granted scopes
        bot_user_id    : the app's bot user id (always present)
        bot_id         : B-prefixed bot id (bot tokens only; None for user tokens)
        acting_user_id : the human this token acts as (user tokens only)
    """
    token = _extract_token(request)
    if not token:
        return None
    st = state()

    # ---- user token (xoxp) → a specific human's DM-reading principal --------
    if token.startswith("xoxp-"):
        row = await st.pool.fetchrow(
            """
            SELECT w.id, w.team_id, w.team_name, w.team_domain, w.bot_user_id,
                   w.signing_secret, w.app_id, w.enterprise_id, w.enterprise_name,
                   w.app_distribution,
                   ut.slack_user_id AS acting_user_id, ut.scopes
              FROM app_slack.user_tokens ut
              JOIN app_slack.workspaces w ON w.id = ut.workspace_id
             WHERE w.run_id = $1
               AND ut.user_token = $2
               AND ut.revoked_at IS NULL
             LIMIT 1
            """,
            st.run_id, token,
        )
        if row is None:
            return None
        ident = dict(row)
        ident["token"] = token
        ident["token_type"] = "user"
        ident["scopes"] = _as_scope_list(ident.get("scopes"))
        ident["bot_id"] = None
        return ident

    # ---- bot token (xoxb / anything else) → the workspace bot principal -----
    row = await st.pool.fetchrow(
        """
        SELECT w.id, w.team_id, w.team_name, w.team_domain, w.bot_user_id,
               w.signing_secret, w.app_id, w.enterprise_id, w.enterprise_name,
               w.app_distribution, i.scopes
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
    scopes: list
    if row is None:
        # Fallback: the workspace's seeded bot_token (no install row yet).
        row = await st.pool.fetchrow(
            """
            SELECT id, team_id, team_name, team_domain, bot_user_id,
                   signing_secret, app_id, enterprise_id, enterprise_name,
                   app_distribution
              FROM app_slack.workspaces
             WHERE run_id = $1 AND bot_token = $2
            """,
            st.run_id, token,
        )
        if row is None:
            return None
        scopes = list(_DEFAULT_BOT_SCOPES)
    else:
        scopes = _as_scope_list(dict(row).get("scopes")) or list(_DEFAULT_BOT_SCOPES)

    ident = dict(row)
    ident.pop("scopes", None)
    ident["token"] = token
    ident["token_type"] = "bot"
    ident["scopes"] = scopes
    ident["acting_user_id"] = None
    ident["bot_id"] = slack_bot_id(ident["bot_user_id"])
    return ident


# Backwards-compatible alias (older imports).
resolve_workspace = resolve_identity


async def require_identity(request: Request) -> Tuple[Optional[dict], Optional[JSONResponse]]:
    """Resolve the principal or build the right Slack auth error.

    Returns ``(identity, None)`` on success, else ``(None, JSONResponse)`` whose
    body is ``not_authed`` (no token at all) or ``invalid_auth`` (bad token) —
    real Slack distinguishes the two.
    """
    ident = await resolve_identity(request)
    if ident is not None:
        return ident, None
    err = "invalid_auth" if _token_present(request) else "not_authed"
    return None, JSONResponse(slack_error(err))


def has_scope(ident: dict, scope: str) -> bool:
    return scope in (ident.get("scopes") or [])


def _as_scope_list(raw) -> list:
    """JSONB scopes come back from asyncpg as a str; coerce to a list."""
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return list(parsed) if isinstance(parsed, list) else []
        except ValueError:
            return []
    return list(raw)


def _token_present(request: Request) -> bool:
    return _extract_token(request) is not None


def _extract_token(request: Request) -> Optional[str]:
    h = request.headers.get("authorization") or request.headers.get("Authorization")
    if h:
        h = h.strip()
        if h.lower().startswith("bearer "):
            tok = h[7:].strip()
            return tok or None
        return h or None
    # Legacy: token may ride in the query string (form-body token is read by
    # routes via read_params, but auth runs first, so we only peek the query).
    tok = request.query_params.get("token")
    return tok or None
