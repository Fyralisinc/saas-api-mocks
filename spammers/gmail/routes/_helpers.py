"""Shared auth → mailbox resolution for Gmail routes."""
from __future__ import annotations

from fastapi import Request

from spammers.common.errors import gmail_error
from spammers.gmail.auth import email_for, mailbox_for_email, resolve_token
from spammers.gmail.responses import GoogleJSONResponse as JSONResponse


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
        return None, JSONResponse(gmail_error(404, "Not Found", reason="notFound"), status_code=404)
    return mbox, None
