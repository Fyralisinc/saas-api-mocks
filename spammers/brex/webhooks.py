"""Outbound Brex webhook delivery (Svix scheme).

When a transfer is processed/failed in Brex, Brex POSTs a Svix-signed webhook.
The money-movement events for a normal (non-Embedded) account are
``TRANSFER_PROCESSED`` / ``TRANSFER_FAILED``; the payload is thin:

  {"event_type":"TRANSFER_PROCESSED", "transfer_id":…, "payment_type":…,
   "return_for_id":null, "company_id":…}

(the full transfer detail lives behind the Payments API ``getTransfersById`` — out
of scope; the consumer correlates ``transfer_id`` against a cash transaction's
``transfer_id`` field instead).

The delivery is signed with **Svix's standard scheme** under Brex's renamed
headers (developer.brex.com/docs/webhooks):

  Webhook-Id:        msg_<id>
  Webhook-Timestamp: <unix_seconds>
  Webhook-Signature: v1,<base64(HMAC-SHA256(key, "{id}.{ts}.{rawBody}"))>

where ``key`` is the base64-decode of the org's ``whsec_…`` secret. Hand that
secret to the consumer as its ``BREX_WEBHOOK_SECRET``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Mapping
from uuid import UUID

import asyncpg
import structlog

from spammers.common.signing import brex_sign
from spammers.common.webhook_emitter import deliver, mark_emitted

log = structlog.get_logger("spammers.brex.webhooks")


def build_event(payload: dict, *, company_id: str) -> dict:
    """Assemble a TRANSFER_PROCESSED / TRANSFER_FAILED webhook event."""
    return {
        "event_type": payload.get("event_type", "TRANSFER_PROCESSED"),
        "transfer_id": payload["transfer_id"],
        "payment_type": payload.get("payment_type", "ACH"),
        "return_for_id": payload.get("return_for_id"),
        "company_id": company_id,
    }


async def emit_event(
    pool: asyncpg.Pool,
    *,
    run_id: UUID,
    event_id: UUID,
    brex_webhook_url: str,
) -> tuple[int, str]:
    """Fetch a ``brex.transfer`` timeline event and POST its Svix-signed webhook."""
    ev = await pool.fetchrow(
        "SELECT payload, virtual_ts FROM timeline.events WHERE id = $1", event_id)
    if ev is None:
        raise LookupError(f"brex timeline event not found: {event_id}")
    payload = ev["payload"] if isinstance(ev["payload"], dict) else json.loads(ev["payload"])

    org = await pool.fetchrow(
        "SELECT company_id, webhook_secret FROM app_brex.organizations WHERE run_id = $1",
        run_id)
    if org is None:
        raise LookupError(f"no brex organization for run {run_id}")

    occurred = ev["virtual_ts"] or datetime.now(timezone.utc)
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=timezone.utc)
    envelope = build_event(payload, company_id=org["company_id"])
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    secret = org["webhook_secret"]
    msg_id = f"msg_{event_id.hex[:24]}"
    ts = int(occurred.timestamp())

    def sign(b: bytes) -> Mapping[str, str]:
        return {
            "Webhook-Id": msg_id,
            "Webhook-Timestamp": str(ts),
            "Webhook-Signature": brex_sign(secret, b, msg_id=msg_id, timestamp=ts),
        }

    status, text = await deliver(url=brex_webhook_url, body=body, sign=sign)
    await mark_emitted(pool, event_id, status=status, attempt_at=datetime.now(timezone.utc))
    log.info("brex_event_emitted", event_id=str(event_id),
             transfer_id=payload.get("transfer_id"),
             event_type=envelope["event_type"], status=status)
    return status, text
