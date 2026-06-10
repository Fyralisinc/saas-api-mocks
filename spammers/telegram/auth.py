"""Auth for the Telegram mock — the persisted MTProto session credential.

Telegram does NOT use an OAuth bearer token: the durable credential is a
persisted Telethon ``StringSession`` (wrapping the DH-negotiated ``auth_key``).
On the real wire that credential authenticates the *connection* (it is never
sent per-request); in this method-contract shim the client presents it on each
HTTP read and once on the WS connect — the transport substitution. Fyralis's
spammer mode presets the session to ``spam-telegram`` (build_telegram_client), so
that is the credential the seeded install carries by default.

A missing / wrong session is the analog of an unauthorized (revoked) session:
the real client's ``is_user_authorized()`` returns False and Telethon raises
``AUTH_KEY_UNREGISTERED`` (RPC, code 401). We surface that as HTTP 401 + the
``AUTH_KEY_UNREGISTERED`` rpc_error so the consumer branches as it would against
the real client (Fyralis maps it to ``telegram_api_unauthorized``).
"""
from __future__ import annotations

from typing import Optional


def extract_session(*, authorization: Optional[str],
                    x_telegram_session: Optional[str],
                    query_session: Optional[str] = None) -> Optional[str]:
    """Pull the session string from the request.

    Accepts ``Authorization: Session <s>`` (the natural per-request stand-in for
    the connection credential), ``Authorization: Bearer <s>``, an
    ``X-Telegram-Session: <s>`` header, or a ``session=`` query param (the WS
    connect path, where headers are awkward to set on some clients).
    """
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() in ("session", "bearer"):
            return parts[1].strip()
        # A bare token with no scheme is also accepted.
        if len(parts) == 1:
            return parts[0].strip()
    if x_telegram_session:
        return x_telegram_session.strip()
    if query_session:
        return query_session.strip()
    return None


def session_ok(presented: Optional[str], expected: str) -> bool:
    """Constant-ish equality check of the presented session vs the install's."""
    return bool(presented) and presented == expected
