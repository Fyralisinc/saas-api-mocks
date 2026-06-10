"""Auth for the Signal mock — the persisted linked-device session credential.

Signal does NOT use an OAuth bearer token: the durable credential is a
persisted libsignal identity/session store (signal-cli's linked-device
registration on disk, keyed by the account number). On the real wire that
credential authenticates the *connection* (a signal-cli daemon holds the linked
device; callers never resend it per-message); in this method-contract shim the
client presents it on each HTTP read and once on the WS connect — the transport
substitution. Fyralis's spammer mode presets the session to ``spam-signal``
(build_signal_client), so that is the credential the seeded install carries.

A missing / wrong session is the analog of a missing/revoked linked device:
the real ``SignalClient`` raises ``SignalApiError(code="signal_api_unauthorized")``.
We surface that as HTTP 401 + a JSON-RPC error whose ``data.signal_code`` is
``signal_api_unauthorized`` so the consumer branches as it would against the real
client (Fyralis maps it to that code).
"""
from __future__ import annotations

from typing import Optional


def extract_session(*, authorization: Optional[str],
                    x_signal_session: Optional[str],
                    query_session: Optional[str] = None) -> Optional[str]:
    """Pull the linked-device session string from the request.

    Accepts ``Authorization: Session <s>`` (the natural per-request stand-in for
    the connection credential), ``Authorization: Bearer <s>``, an
    ``X-Signal-Session: <s>`` header, or a ``session=`` query param (the WS
    connect path, where headers are awkward to set on some clients).
    """
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() in ("session", "bearer"):
            return parts[1].strip()
        # A bare token with no scheme is also accepted.
        if len(parts) == 1:
            return parts[0].strip()
    if x_signal_session:
        return x_signal_session.strip()
    if query_session:
        return query_session.strip()
    return None


def session_ok(presented: Optional[str], expected: str) -> bool:
    """Constant-ish equality check of the presented session vs the install's."""
    return bool(presented) and presented == expected
