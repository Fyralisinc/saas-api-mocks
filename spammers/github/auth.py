"""Installation-token resolution for the GitHub mock.

REST endpoints authenticate with an installation access token (``ghs_…``),
minted by ``POST /app/installations/{id}/access_tokens`` and recorded in
``oauth.installs`` (provider ``github``, ``provider_account_id`` = installation
id). GitHub accepts both ``Authorization: Bearer <token>`` and the legacy
``Authorization: token <token>``.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Request

from spammers.github.state import state


def bearer(request: Request) -> Optional[str]:
    h = request.headers.get("authorization") or request.headers.get("Authorization")
    if not h:
        return None
    h = h.strip()
    low = h.lower()
    if low.startswith("bearer "):
        return h[7:].strip()
    if low.startswith("token "):
        return h[6:].strip()
    return h


async def resolve_installation(request: Request) -> Optional[dict]:
    """Resolve the installation behind a ``ghs_…`` token, or None."""
    token = bearer(request)
    if not token or not token.startswith("ghs_"):
        return None
    st = state()
    row = await st.pool.fetchrow(
        """
        SELECT inst.id AS installation_pk, inst.installation_id, inst.account_login,
               inst.account_type, inst.account_id, inst.repository_selection,
               a.app_id, a.id AS app_pk
          FROM oauth.installs oi
          JOIN app_github.apps a
            ON a.run_id = oi.run_id
          JOIN app_github.installations inst
            ON inst.app_pk = a.id
           AND inst.installation_id::text = oi.provider_account_id
         WHERE oi.run_id = $1
           AND oi.provider = 'github'
           AND oi.access_token = $2
           AND oi.revoked_at IS NULL
           AND (oi.expires_at IS NULL OR oi.expires_at > now())
         LIMIT 1
        """,
        st.run_id, token,
    )
    return dict(row) if row else None
