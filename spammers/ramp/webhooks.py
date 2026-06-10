"""Outbound Ramp webhook delivery (``X-Ramp-Signature`` HMAC scheme).

When a transaction clears/declines/syncs in Ramp, Ramp POSTs a **thin** event —
it carries only the resource id, and the consumer fetches the full record
(fetch-on-notify ``GET /developer/v1/transactions/{id}``):

  {"id": <event id, constant across retries>,
   "type": "transactions.cleared",
   "created_at": "2026-…+00:00",
   "business_id": <the Ramp business uuid>,
   "object": {"id": <transaction id>}}

The delivery is signed with a single header (docs.ramp.com/.../webhooks):

  X-Ramp-Signature: <bare lowercase hex HMAC-SHA256(secret, rawBody)>

— NO ``sha256=`` / ``v1,`` prefix, NOT base64, NO timestamp in the signed bytes
(the simplest HMAC shape: GitHub's ``X-Hub-Signature-256`` minus the prefix).
Hand that secret to the consumer as its ``RAMP_WEBHOOK_SECRET``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Mapping
from uuid import UUID

import asyncpg
import structlog

from spammers.common.signing import ramp_sign
from spammers.common.webhook_emitter import deliver, mark_emitted

log = structlog.get_logger("spammers.ramp.webhooks")


def build_event(payload: dict, *, business_id: str, event_id: str,
                created_at: datetime) -> dict:
    """Assemble a thin Ramp webhook event (carries only the resource id)."""
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return {
        "id": event_id,
        "type": payload.get("event_type", "transactions.cleared"),
        "created_at": created_at.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
        "business_id": business_id,
        "object": {"id": payload["txn_id"]},
    }


async def emit_event(
    pool: asyncpg.Pool,
    *,
    run_id: UUID,
    event_id: UUID,
    ramp_webhook_url: str,
) -> tuple[int, str]:
    """Fetch a ``ramp.transaction`` timeline event and POST its signed webhook."""
    ev = await pool.fetchrow(
        "SELECT payload, virtual_ts FROM timeline.events WHERE id = $1", event_id)
    if ev is None:
        raise LookupError(f"ramp timeline event not found: {event_id}")
    payload = ev["payload"] if isinstance(ev["payload"], dict) else json.loads(ev["payload"])

    org = await pool.fetchrow(
        "SELECT business_id, webhook_secret FROM app_ramp.organizations WHERE run_id = $1",
        run_id)
    if org is None:
        raise LookupError(f"no ramp organization for run {run_id}")

    occurred = ev["virtual_ts"] or datetime.now(timezone.utc)
    envelope = build_event(payload, business_id=org["business_id"],
                           event_id=f"wbhe_{event_id.hex[:24]}", created_at=occurred)
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    secret = org["webhook_secret"]

    def sign(b: bytes) -> Mapping[str, str]:
        return {"X-Ramp-Signature": ramp_sign(secret, b)}

    status, text = await deliver(url=ramp_webhook_url, body=body, sign=sign)
    await mark_emitted(pool, event_id, status=status, attempt_at=datetime.now(timezone.utc))
    log.info("ramp_event_emitted", event_id=str(event_id),
             txn_id=payload.get("txn_id"), event_type=envelope["type"], status=status)
    return status, text
