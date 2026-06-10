"""Outbound HiBob webhook delivery (``Bob-Signature`` HMAC-SHA512 scheme).

When an HR change happens in HiBob, HiBob POSTs an HMAC-signed **Webhooks v2**
payload (apidocs.hibob.com/changelog/introducing-bob-webhooks-v2). The v2
envelope is **metadata-only** — it carries IDs + field-update ids, NOT the full
employee/time-off object:

  {"companyId": 636192,
   "type": "employee.updated",
   "triggeredBy": "3498867108266115473",
   "triggeredAt": "2024-12-30T12:56:18.955603",
   "version": "v2",
   "data": {"employeeId": "…", "fieldUpdatesIds": [{"id": "root.surname"}]}}

(for a time-off event ``data`` = ``{timeoffRequestId, employeeId, getApi:<url>}``
— a fetch URL rather than the record). The delivery is signed:

  Bob-Signature: base64(HMAC-SHA512(secret, rawBody))   (no prefix, no timestamp)

i.e. a base64 HMAC-SHA512 over the raw body alone. Hand the signing secret to the
consumer as its ``HIBOB_WEBHOOK_SECRET``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Mapping
from uuid import UUID

import asyncpg
import structlog

from spammers.common.signing import hibob_sign
from spammers.common.webhook_emitter import deliver, mark_emitted
from spammers.hibob import dto as _dto

log = structlog.get_logger("spammers.hibob.webhooks")

_WEBHOOK_VERSION = "v2"


def build_event(payload: dict, *, company_id: str, occurred: datetime) -> dict:
    """Assemble the v2 ``{companyId, type, triggeredBy, triggeredAt, version, data}`` envelope."""
    try:
        cid: int | str = int(company_id)
    except (TypeError, ValueError):
        cid = company_id   # companyId is a NUMBER on the wire; fall back to string if non-numeric
    return {
        "companyId": cid,
        "type": payload.get("event_type", "employee.updated"),
        "triggeredBy": payload.get("triggered_by", "system"),
        "triggeredAt": _dto.iso_noz(occurred),
        "version": _WEBHOOK_VERSION,
        "data": payload.get("data", {}),
    }


async def emit_event(
    pool: asyncpg.Pool,
    *,
    run_id: UUID,
    event_id: UUID,
    hibob_webhook_url: str,
) -> tuple[int, str]:
    """Fetch a ``hibob.event`` timeline event and POST its HMAC-signed webhook."""
    ev = await pool.fetchrow(
        "SELECT payload, virtual_ts FROM timeline.events WHERE id = $1", event_id)
    if ev is None:
        raise LookupError(f"hibob timeline event not found: {event_id}")
    payload = ev["payload"] if isinstance(ev["payload"], dict) else json.loads(ev["payload"])

    co = await pool.fetchrow(
        "SELECT company_id, webhook_secret FROM app_hibob.companies WHERE run_id = $1",
        run_id)
    if co is None:
        raise LookupError(f"no hibob company for run {run_id}")

    occurred = ev["virtual_ts"] or datetime.now(timezone.utc)
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=timezone.utc)
    envelope = build_event(payload, company_id=co["company_id"], occurred=occurred)
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    secret = co["webhook_secret"]

    def sign(b: bytes) -> Mapping[str, str]:
        return {"Bob-Signature": hibob_sign(secret, b)}

    status, text = await deliver(url=hibob_webhook_url, body=body, sign=sign)
    await mark_emitted(pool, event_id, status=status, attempt_at=datetime.now(timezone.utc))
    log.info("hibob_event_emitted", event_id=str(event_id),
             event_type=envelope["type"], status=status)
    return status, text
