"""Registry of Gateway sessions + the fan-out path.

Two maps:
  - ``live``: session_id -> GatewaySession for currently-connected clients.
  - ``resumable``: session_id -> GatewaySession parked after a disconnect,
    retained (with its ring buffer) until ``resume_deadline`` so a RESUME can
    replay missed dispatches.

The ``GatewayDispatcher`` calls :meth:`fan_out` to push ``MESSAGE_CREATE`` (and
other events) to matching live sessions. Fan-out never blocks on a slow client:
a full outbound queue drops that one session (4009) and moves on.
"""
from __future__ import annotations

import time
from typing import Callable, Optional
from uuid import UUID

import structlog

from spammers.discord.gateway.opcodes import CloseCode
from spammers.discord.gateway.session import GatewaySession

log = structlog.get_logger("spammers.discord.hub")

# How long a disconnected session stays resumable (seconds). Real Discord is a
# few minutes; kept short + injectable for test determinism.
DEFAULT_RESUME_TTL_S = 60.0


class SessionHub:
    def __init__(self, *, resume_ttl_s: float = DEFAULT_RESUME_TTL_S) -> None:
        self.live: dict[str, GatewaySession] = {}
        self.resumable: dict[str, GatewaySession] = {}
        self.resume_ttl_s = resume_ttl_s

    # ----- lifecycle -----

    def register(self, session: GatewaySession) -> None:
        self.live[session.session_id] = session
        self.resumable.pop(session.session_id, None)

    def park(self, session: GatewaySession) -> None:
        """Move a disconnected session into the resumable set with a TTL."""
        session.connected = False
        session.resume_deadline = time.monotonic() + self.resume_ttl_s
        self.live.pop(session.session_id, None)
        self.resumable[session.session_id] = session

    def drop(self, session_id: str) -> None:
        self.live.pop(session_id, None)
        self.resumable.pop(session_id, None)

    def take_resumable(self, session_id: str) -> Optional[GatewaySession]:
        """Pop a still-valid resumable session (None if unknown or expired)."""
        s = self.resumable.get(session_id)
        if s is None:
            return None
        if s.resume_deadline is not None and time.monotonic() > s.resume_deadline:
            self.resumable.pop(session_id, None)
            return None
        self.resumable.pop(session_id, None)
        return s

    def reap(self) -> int:
        """Evict expired resumable sessions. Returns the count evicted."""
        now = time.monotonic()
        expired = [
            sid for sid, s in self.resumable.items()
            if s.resume_deadline is not None and now > s.resume_deadline
        ]
        for sid in expired:
            self.resumable.pop(sid, None)
        return len(expired)

    async def close_all(self) -> None:
        for s in list(self.live.values()):
            s.request_close(CloseCode.UNKNOWN_ERROR.value)
        self.live.clear()
        self.resumable.clear()

    # ----- fan-out -----

    def fan_out(
        self,
        application_pk: UUID,
        t: str,
        payload_for: Callable[[GatewaySession], dict],
        *,
        required_intent: int = 0,
    ) -> int:
        """Dispatch event ``t`` to every live session of ``application_pk``.

        ``payload_for(session)`` builds the per-session ``d`` (so intent-based
        content gating happens per subscriber). Sessions lacking
        ``required_intent`` are skipped. A full queue drops a live session.

        Disconnected-but-resumable sessions also receive the event into their
        ring buffer (without enqueuing) so a subsequent RESUME replays what was
        missed — matching real Discord. Returns the count delivered live.
        """
        delivered = 0
        for session in list(self.live.values()):
            if session.application_pk != application_pk:
                continue
            if required_intent and not (session.intents & required_intent):
                continue
            d = payload_for(session)
            try:
                session.dispatch(t, d)
            except Exception:  # asyncio.QueueFull (or anything else) → drop client
                log.warning("gateway_fanout_drop", session_id=session.session_id)
                session.request_close(CloseCode.SESSION_TIMED_OUT.value)
                continue
            delivered += 1

        for session in list(self.resumable.values()):
            if session.application_pk != application_pk:
                continue
            if required_intent and not (session.intents & required_intent):
                continue
            session.buffer_dispatch(t, payload_for(session))
        return delivered
