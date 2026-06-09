"""Outbound QuickBooks Online webhook delivery (Intuit eventNotifications).

When the orchestrator drains a live ``quickbooks.change`` timeline event, this
builds Intuit's THIN change notification:

  {"eventNotifications":[{"realmId":...,"dataChangeEvent":{"entities":[
     {"name":"Bill","id":...,"operation":"Create","lastUpdated":"...-0000"}]}}]}

and signs it ``intuit-signature: base64(HMAC-SHA256(rawBody, verifierToken))``
(base64, NOT hex — the real QBO scheme), then POSTs it to the consumer. The
notification carries no body, so the consumer re-queries the entity.

The mock's verifier token is the fixed `MOCK_VERIFIER` below — hand it to the
consumer as its `QUICKBOOKS_WEBHOOK_VERIFIER`.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Mapping
from uuid import UUID

import asyncpg
import structlog

from spammers.common.signing import intuit_sign
from spammers.common.webhook_emitter import deliver, mark_emitted

log = structlog.get_logger("spammers.quickbooks.webhooks")

# Fixed mock webhook verifier token (real QBO: configured per app in the portal).
MOCK_VERIFIER = "qbo-mock-verifier-7f3a9c2e"


def _compact_offset(dt: datetime) -> str:
    """Intuit's webhook ``lastUpdated`` uses a compact offset (-0700, no colon)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S%z")  # %z -> +0000 (no colon)


async def emit_event(
    pool: asyncpg.Pool,
    *,
    run_id: UUID,
    event_id: UUID,
    quickbooks_webhook_url: str,
) -> tuple[int, str]:
    """Fetch a ``quickbooks.change`` timeline event and POST its signed webhook."""
    ev = await pool.fetchrow(
        "SELECT payload, virtual_ts FROM timeline.events WHERE id = $1", event_id)
    if ev is None:
        raise LookupError(f"quickbooks timeline event not found: {event_id}")
    payload = ev["payload"] if isinstance(ev["payload"], dict) else json.loads(ev["payload"])
    realm_id = payload.get("realm_id")
    entity = {
        "name": payload["entity_name"],
        "id": payload["entity_id"],
        "operation": payload.get("operation", "Create"),
        "lastUpdated": _compact_offset(ev["virtual_ts"] or datetime.now(timezone.utc)),
    }
    envelope = {
        "eventNotifications": [{
            "realmId": realm_id,
            "dataChangeEvent": {"entities": [entity]},
        }],
    }
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")

    def sign(b: bytes) -> Mapping[str, str]:
        return {"intuit-signature": intuit_sign(MOCK_VERIFIER, b)}

    status, text = await deliver(url=quickbooks_webhook_url, body=body, sign=sign)
    await mark_emitted(pool, event_id, status=status, attempt_at=datetime.now(timezone.utc))
    log.info("quickbooks_event_emitted", event_id=str(event_id),
             entity=entity["name"], entity_id=entity["id"], status=status)
    return status, text
