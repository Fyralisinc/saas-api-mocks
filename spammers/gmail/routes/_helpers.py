"""Shared auth → mailbox resolution for Gmail routes."""
from __future__ import annotations

from fastapi import Request

from spammers.common.errors import gmail_error
from spammers.gmail.auth import email_for, mailbox_for_email, resolve_token
from spammers.gmail.responses import GoogleJSONResponse as JSONResponse
from spammers.gmail.state import state

# A sentinel mailbox_pk that matches no row — an EMPTY mailbox for a valid
# Workspace user who has no seeded mail (every read query returns zero rows).
_EMPTY_MBOX_ID = "00000000-0000-0000-0000-000000000000"


async def _person_in_run(email: str) -> bool:
    st = state()
    row = await st.pool.fetchrow(
        "SELECT 1 FROM org.people WHERE run_id = $1 AND email = $2", st.run_id, email)
    return row is not None


async def require_mailbox(request: Request, user_id: str):
    """Return ``(mailbox_row, None)`` or ``(None, error_response)``."""
    claims = resolve_token(request)
    if claims is None:
        return None, JSONResponse(gmail_error(401, "Invalid Credentials", reason="authError"),
                                  status_code=401)
    email = email_for(claims, user_id)
    if not email:
        return None, JSONResponse(gmail_error(400, "Bad Request", reason="badRequest"), status_code=400)
    mbox = await mailbox_for_email(email)
    if mbox is None:
        # Every real Workspace user has a mailbox: a valid directory user with no
        # seeded mail behaves as an EMPTY mailbox (200), not a 404. Only a user
        # who isn't in the domain at all is a genuine 404.
        if await _person_in_run(email):
            return {"id": _EMPTY_MBOX_ID, "email": email, "history_id": 1,
                    "profile": {"messagesTotal": 0, "threadsTotal": 0}}, None
        return None, JSONResponse(gmail_error(404, "Not Found", reason="notFound"), status_code=404)
    return mbox, None
