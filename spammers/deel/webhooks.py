"""Outbound Deel webhook delivery (``x-deel-signature`` HMAC scheme).

When a contract/invoice changes in Deel, Deel POSTs an HMAC-signed webhook. The
payload is a nested envelope (developer.deel.com/api/webhooks):

  {"data": {"meta": {"event_type": "contract.updated", "organization_id": "…"},
            "resource": [ { …the contract/invoice… } ]},
   "timestamp": "2025-02-05T15:39:38.070Z"}

— the event type lives at ``data.meta.event_type`` (a dotted name), the tenant id
at ``data.meta.organization_id``, and the changed object is an ARRAY under
``data.resource`` (NOT a flat ``{event_type, data}``).

The delivery is signed with Deel's scheme (developer.deel.com webhook verification):

  x-deel-signature:       <hex(HMAC-SHA256(secret, "POST" + rawBody))>   (bare hex, no prefix)
  x-deel-hmac-label:      <which signing key>
  x-deel-webhook-version: <serialization version>

i.e. the literal method string ``POST`` is prepended to the raw body before the
HMAC, the digest is bare lowercase hex (NOT ``sha256=``, NOT base64), and there is
NO timestamp in the signed string. Hand the signing secret to the consumer as its
``DEEL_WEBHOOK_SECRET``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Mapping
from uuid import UUID

import asyncpg
import structlog

from spammers.common.signing import deel_sign
from spammers.common.webhook_emitter import deliver, mark_emitted
from spammers.deel import dto as _dto

log = structlog.get_logger("spammers.deel.webhooks")

_HMAC_LABEL = "primary"
_WEBHOOK_VERSION = "2"


def build_event(payload: dict, *, organization_id: str,
                occurred: datetime) -> dict:
    """Assemble the nested ``{data:{meta, resource:[…]}, timestamp}`` Deel envelope."""
    resource = payload.get("resource")
    if resource is None:
        resource = []
    elif not isinstance(resource, list):
        resource = [resource]
    return {
        "data": {
            "meta": {
                "event_type": payload.get("event_type", "contract.updated"),
                "organization_id": organization_id,
            },
            "resource": resource,
        },
        "timestamp": _dto._ts(occurred),
    }


async def emit_event(
    pool: asyncpg.Pool,
    *,
    run_id: UUID,
    event_id: UUID,
    deel_webhook_url: str,
) -> tuple[int, str]:
    """Fetch a ``deel.event`` timeline event and POST its HMAC-signed webhook."""
    ev = await pool.fetchrow(
        "SELECT payload, virtual_ts FROM timeline.events WHERE id = $1", event_id)
    if ev is None:
        raise LookupError(f"deel timeline event not found: {event_id}")
    payload = ev["payload"] if isinstance(ev["payload"], dict) else json.loads(ev["payload"])

    org = await pool.fetchrow(
        "SELECT organization_id, webhook_secret FROM app_deel.organizations WHERE run_id = $1",
        run_id)
    if org is None:
        raise LookupError(f"no deel organization for run {run_id}")

    occurred = ev["virtual_ts"] or datetime.now(timezone.utc)
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=timezone.utc)
    envelope = build_event(payload, organization_id=org["organization_id"], occurred=occurred)
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    secret = org["webhook_secret"]

    def sign(b: bytes) -> Mapping[str, str]:
        return {
            "x-deel-signature": deel_sign(secret, b),
            "x-deel-hmac-label": _HMAC_LABEL,
            "x-deel-webhook-version": _WEBHOOK_VERSION,
        }

    status, text = await deliver(url=deel_webhook_url, body=body, sign=sign)
    await mark_emitted(pool, event_id, status=status, attempt_at=datetime.now(timezone.utc))
    log.info("deel_event_emitted", event_id=str(event_id),
             event_type=envelope["data"]["meta"]["event_type"], status=status)
    return status, text
