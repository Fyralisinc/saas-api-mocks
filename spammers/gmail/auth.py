"""Bearer/DWD-token auth + mailbox resolution for the Gmail mock."""
from __future__ import annotations

from typing import Optional

from fastapi import Request

from spammers.common.google_token import decode_access_token
from spammers.gmail.state import state


def bearer(request: Request) -> Optional[str]:
    h = request.headers.get("authorization") or request.headers.get("Authorization")
    if not h:
        return None
    h = h.strip()
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    return h


def resolve_token(request: Request) -> Optional[dict]:
    tok = bearer(request)
    if not tok:
        return None
    return decode_access_token(tok)


async def mailbox_for_email(email: str) -> Optional[dict]:
    st = state()
    return await st.pool.fetchrow(
        """
        SELECT m.* FROM app_gmail.mailboxes m
          JOIN app_gmail.customers c ON c.id = m.customer_pk
         WHERE c.run_id = $1 AND m.email = $2
        """,
        st.run_id, email,
    )


def email_for(claims: dict, user_id: str) -> Optional[str]:
    return claims.get("sub") if user_id in ("me", "") else user_id
