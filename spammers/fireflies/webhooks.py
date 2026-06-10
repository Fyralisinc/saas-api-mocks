"""Outbound Fireflies webhook delivery (``x-hub-signature`` HMAC scheme, V2).

When a meeting transcript completes, Fireflies POSTs a **thin** event — it carries
only the meeting id + an event name, and the consumer fetch-on-notify hydrates the
full transcript via the GraphQL ``transcript(id:)`` query. The current (V2) shape
(docs.fireflies.ai/graphql-api/webhooks-v2):

  {"event": "meeting.transcribed",
   "timestamp": <unix MILLISECONDS, Number>,
   "meeting_id": "<transcript id>",
   "client_reference_id": "<optional custom id>"}

(The legacy V1 shape was ``{"meetingId", "eventType":"Transcription completed",
"clientReferenceId"}`` — we model V2, the current scheme.) Neither shape carries a
``workspaceId`` — there is no first-class workspace id; the real edge resolves the
tenant from the per-secret subscription, not a body field.

The delivery is signed with one header:

  x-hub-signature: sha256=<hex HMAC-SHA256(secret, rawBody)>

— the legacy ``x-hub-signature`` header NAME but a SHA-256 digest with the
``sha256=`` prefix, over the raw body alone (no timestamp in the signed bytes).
Hand that secret to the consumer as its ``FIREFLIES_WEBHOOK_SECRET``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Mapping
from uuid import UUID

import asyncpg
import structlog

from spammers.common.signing import fireflies_sign
from spammers.common.webhook_emitter import deliver, mark_emitted

log = structlog.get_logger("spammers.fireflies.webhooks")


def build_event(payload: dict, *, created_at: datetime) -> dict:
    """Assemble a thin Fireflies V2 webhook event (carries only the meeting id)."""
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    ev = {
        "event": payload.get("event_type", "meeting.transcribed"),
        "timestamp": int(created_at.astimezone(timezone.utc).timestamp() * 1000),
        "meeting_id": payload["transcript_id"],
    }
    cref = payload.get("client_reference_id")
    if cref:
        ev["client_reference_id"] = cref
    return ev


async def emit_event(
    pool: asyncpg.Pool,
    *,
    run_id: UUID,
    event_id: UUID,
    fireflies_webhook_url: str,
) -> tuple[int, str]:
    """Fetch a ``fireflies.transcript`` timeline event and POST its signed webhook."""
    ev = await pool.fetchrow(
        "SELECT payload, virtual_ts FROM timeline.events WHERE id = $1", event_id)
    if ev is None:
        raise LookupError(f"fireflies timeline event not found: {event_id}")
    payload = ev["payload"] if isinstance(ev["payload"], dict) else json.loads(ev["payload"])

    ws = await pool.fetchrow(
        "SELECT webhook_secret FROM app_fireflies.workspaces WHERE run_id = $1", run_id)
    if ws is None:
        raise LookupError(f"no fireflies workspace for run {run_id}")

    occurred = ev["virtual_ts"] or datetime.now(timezone.utc)
    envelope = build_event(payload, created_at=occurred)
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    secret = ws["webhook_secret"]

    def sign(b: bytes) -> Mapping[str, str]:
        return {"x-hub-signature": fireflies_sign(secret, b)}

    status, text = await deliver(url=fireflies_webhook_url, body=body, sign=sign)
    await mark_emitted(pool, event_id, status=status, attempt_at=datetime.now(timezone.utc))
    log.info("fireflies_event_emitted", event_id=str(event_id),
             transcript_id=payload.get("transcript_id"),
             ff_event=envelope["event"], status=status)
    return status, text


app = None  # this module is delivery-only (no FastAPI app)
