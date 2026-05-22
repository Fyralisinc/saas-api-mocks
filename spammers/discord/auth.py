"""Bot-token resolution for the Discord mock.

Discord REST calls authenticate with ``Authorization: Bot <token>`` (the legacy
``Bearer <token>`` form — an OAuth2 access token — is also accepted). The token
is matched against ``app_discord.applications.bot_token`` for the active run.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Request

from spammers.discord.state import state


def extract_token(request: Request) -> Optional[str]:
    """Return the raw token from an ``Authorization: Bot|Bearer <token>`` header."""
    h = request.headers.get("authorization") or request.headers.get("Authorization")
    if not h:
        return None
    h = h.strip()
    low = h.lower()
    if low.startswith("bot "):
        return h[4:].strip()
    if low.startswith("bearer "):
        return h[7:].strip()
    return h


async def resolve_application(request: Request) -> Optional[dict]:
    """Resolve the application behind a bot token, or None on bad auth."""
    token = extract_token(request)
    if not token:
        return None
    st = state()
    row = await st.pool.fetchrow(
        """
        SELECT a.id AS application_pk, a.application_id, a.client_id, a.bot_token,
               a.public_key, a.private_key,
               g.id AS guild_pk, g.guild_id
          FROM app_discord.applications a
          LEFT JOIN app_discord.guilds g ON g.application_pk = a.id
         WHERE a.run_id = $1 AND a.bot_token = $2
         LIMIT 1
        """,
        st.run_id, token,
    )
    return dict(row) if row else None
