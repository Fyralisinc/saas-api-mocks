"""Outbound Mercury transaction-webhook delivery.

When the orchestrator drains a live ``mercury.transaction`` timeline event, this
builds Mercury's JSON-merge-patch event (the same envelope the Events API emits):

  {"id":…, "resourceType":"transaction", "resourceId":…, "operationType":"create",
   "resourceVersion":1, "occurredAt":"…Z", "changedPaths":[…], "mergePatch":{…},
   "previousValues":{…}}

and signs it ``Mercury-Signature: t=<unix_seconds>,v1=<hex>`` — Stripe-style, where
the hex is HMAC-SHA256 over ``"{t}.{rawBody}"`` (bare hex, no ``sha256=`` prefix).

For a ``create`` the ``mergePatch`` is the full transaction (RFC-7396 merge patch
over an empty resource), so the consumer can apply it directly OR re-fetch
``GET /account/{accountId}/transaction/{id}`` (the patch carries ``accountId``).
For an ``update`` the patch is just the changed fields (e.g. status pending→sent).

The mock's signing key is the org row's ``webhook_secret`` — hand it to the
consumer as its ``MERCURY_WEBHOOK_SECRET``. (Mercury's ``occurredAt`` uses
MICROSECOND precision — distinct from the REST bodies' seconds precision.)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Mapping
from uuid import UUID

import asyncpg
import structlog

from spammers.common.signing import mercury_sign
from spammers.common.webhook_emitter import deliver, mark_emitted

log = structlog.get_logger("spammers.mercury.webhooks")


def _occurred_at(dt: datetime) -> str:
    """Mercury's webhook ``occurredAt`` — RFC3339 UTC with microseconds + ``Z``."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def build_event(payload: dict, *, event_id: UUID, occurred_at: datetime) -> dict:
    """Assemble the JSON-merge-patch transaction event from a thin timeline payload."""
    operation = payload.get("operation", "create")
    merge_patch = payload.get("merge_patch") or {}
    changed = payload.get("changed_paths") or sorted(merge_patch.keys())
    return {
        "id": str(event_id),
        "resourceType": "transaction",
        "resourceId": payload["txn_id"],
        "operationType": operation,
        "resourceVersion": int(payload.get("resource_version", 1)),
        "occurredAt": _occurred_at(occurred_at),
        "changedPaths": changed,
        "mergePatch": merge_patch,
        "previousValues": payload.get("previous_values") or {},
    }


async def emit_event(
    pool: asyncpg.Pool,
    *,
    run_id: UUID,
    event_id: UUID,
    mercury_webhook_url: str,
) -> tuple[int, str]:
    """Fetch a ``mercury.transaction`` timeline event and POST its signed webhook."""
    ev = await pool.fetchrow(
        "SELECT payload, virtual_ts FROM timeline.events WHERE id = $1", event_id)
    if ev is None:
        raise LookupError(f"mercury timeline event not found: {event_id}")
    payload = ev["payload"] if isinstance(ev["payload"], dict) else json.loads(ev["payload"])

    org = await pool.fetchrow(
        "SELECT webhook_secret FROM app_mercury.organizations WHERE run_id = $1", run_id)
    if org is None:
        raise LookupError(f"no mercury organization for run {run_id}")

    occurred = ev["virtual_ts"] or datetime.now(timezone.utc)
    envelope = build_event(payload, event_id=event_id, occurred_at=occurred)
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    secret = org["webhook_secret"]
    ts = int(occurred.replace(tzinfo=occurred.tzinfo or timezone.utc).timestamp())

    def sign(b: bytes) -> Mapping[str, str]:
        return {"Mercury-Signature": mercury_sign(secret, b, ts)}

    status, text = await deliver(url=mercury_webhook_url, body=body, sign=sign)
    await mark_emitted(pool, event_id, status=status, attempt_at=datetime.now(timezone.utc))
    log.info("mercury_event_emitted", event_id=str(event_id),
             txn_id=payload.get("txn_id"), operation=envelope["operationType"], status=status)
    return status, text
