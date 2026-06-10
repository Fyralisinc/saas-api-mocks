"""Outbound Gusto webhook delivery (``X-Gusto-Signature`` HMAC scheme).

When a payroll is processed / an employee changes, Gusto POSTs a **THIN** event —
it carries only resource/entity references (no full body), so the consumer
fetch-on-notify pulls the record (``GET /v1/companies/{co}/payrolls/{uuid}``):

  {"uuid": <event id, constant across retries>,
   "event_type": "payroll.processed",
   "resource_type": "Payroll",
   "resource_uuid": <the payroll uuid>,
   "entity_type": "Company",
   "entity_uuid": <the company uuid>,
   "timestamp": 1671058841}              # NB: a numeric Unix EPOCH (not ISO)

The delivery is signed with a single header (docs.gusto.com/embedded-payroll/docs/webhooks):

  X-Gusto-Signature: <lowercase hex HMAC-SHA256(verification_token, rawBody)>

— NO ``sha256=`` prefix, NO timestamp in the signed bytes (GitHub's
``X-Hub-Signature-256`` shape minus the prefix). The secret is the webhook
subscription's ``verification_token``. (The hex-vs-base64 encoding is the one
INFERRED detail — Gusto documents the algorithm/secret but not the encoding;
defaulting to hex. The Fyralis QBO clone wrongly assumes ``Gusto-Signature`` +
base64 — logged in the gusto-fidelity-audit memory.)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Mapping
from uuid import UUID

import asyncpg
import structlog

from spammers.common.signing import gusto_sign
from spammers.common.webhook_emitter import deliver, mark_emitted

log = structlog.get_logger("spammers.gusto.webhooks")

# resource_type -> entity_type (Gusto's resource/entity split for the thin event).
_RESOURCE_ENTITY = {"Payroll": "Company", "Employee": "Company"}


def build_event(payload: dict, *, company_uuid: str, event_id: str,
                occurred: datetime) -> dict:
    """Assemble a thin Gusto webhook event (references only; epoch timestamp)."""
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=timezone.utc)
    resource_type = payload.get("resource_type", "Payroll")
    return {
        "uuid": event_id,
        "event_type": payload.get("event_type", "payroll.processed"),
        "resource_type": resource_type,
        "resource_uuid": payload["resource_uuid"],
        "entity_type": _RESOURCE_ENTITY.get(resource_type, "Company"),
        "entity_uuid": company_uuid,
        "timestamp": int(occurred.timestamp()),
    }


async def emit_event(
    pool: asyncpg.Pool,
    *,
    run_id: UUID,
    event_id: UUID,
    gusto_webhook_url: str,
) -> tuple[int, str]:
    """Fetch a ``gusto.event`` timeline event and POST its signed webhook."""
    ev = await pool.fetchrow(
        "SELECT payload, virtual_ts FROM timeline.events WHERE id = $1", event_id)
    if ev is None:
        raise LookupError(f"gusto timeline event not found: {event_id}")
    payload = ev["payload"] if isinstance(ev["payload"], dict) else json.loads(ev["payload"])

    co = await pool.fetchrow(
        "SELECT company_uuid, webhook_secret FROM app_gusto.companies WHERE run_id = $1",
        run_id)
    if co is None:
        raise LookupError(f"no gusto company for run {run_id}")

    occurred = ev["virtual_ts"] or datetime.now(timezone.utc)
    envelope = build_event(payload, company_uuid=co["company_uuid"],
                           event_id=str(UUID(int=event_id.int)), occurred=occurred)
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    secret = co["webhook_secret"]

    def sign(b: bytes) -> Mapping[str, str]:
        return {"X-Gusto-Signature": gusto_sign(secret, b)}

    status, text = await deliver(url=gusto_webhook_url, body=body, sign=sign)
    await mark_emitted(pool, event_id, status=status, attempt_at=datetime.now(timezone.utc))
    log.info("gusto_event_emitted", event_id=str(event_id),
             resource_uuid=payload.get("resource_uuid"),
             event_type=envelope["event_type"], status=status)
    return status, text
