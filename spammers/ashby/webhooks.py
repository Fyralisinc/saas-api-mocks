"""Outbound Ashby webhook delivery.

When the orchestrator drains a live ``ashby.object`` timeline event, this builds
Ashby's webhook envelope and signs it. The real Ashby webhook is:

    {"action": "<eventType>", "data": { "<entity>": { …full entity… } }}

signed ``Ashby-Signature: sha256=<lowercase-hex(HMAC-SHA256(secret, rawBody))>``
over the RAW request body — the same wire shape as a GitHub ``X-Hub-Signature-256``
(the ``sha256=`` prefix IS present), no timestamp / replay window. The signing key
is the org row's ``webhook_secret`` (hand it to the consumer as ``ASHBY_WEBHOOK_SECRET``).

NOTE: Fyralis's webhook handler resolves the tenant from a body ``organizationId``
field that its own flow doc admits is a synthetic-gate stand-in (real Ashby
tenant-resolution is by the receiving endpoint/secret, NOT a body field). The REAL
Ashby payload carries NO ``organizationId`` — the mock emits the real ``{action,
data}`` shape, and that Fyralis-vs-real difference is LOGGED in the memory entry,
not papered over here.

A ``create``/``submit`` event carries the FULL entity body, so the consumer can
build its draft directly (or re-fetch via ``<category>.info``, which the row backs).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Mapping
from uuid import UUID

import asyncpg
import structlog

from spammers.common.signing import ashby_sign
from spammers.common.webhook_emitter import deliver, mark_emitted

log = structlog.get_logger("spammers.ashby.webhooks")


def build_event(payload: dict) -> dict:
    """Assemble the ``{action, data}`` webhook envelope from a thin timeline payload."""
    return {"action": payload["action"], "data": payload.get("data") or {}}


async def emit_event(
    pool: asyncpg.Pool,
    *,
    run_id: UUID,
    event_id: UUID,
    ashby_webhook_url: str,
) -> tuple[int, str]:
    """Fetch an ``ashby.object`` timeline event and POST its signed webhook."""
    ev = await pool.fetchrow(
        "SELECT payload FROM timeline.events WHERE id = $1", event_id)
    if ev is None:
        raise LookupError(f"ashby timeline event not found: {event_id}")
    payload = ev["payload"] if isinstance(ev["payload"], dict) else json.loads(ev["payload"])

    org = await pool.fetchrow(
        "SELECT webhook_secret FROM app_ashby.organizations WHERE run_id = $1", run_id)
    if org is None:
        raise LookupError(f"no ashby organization for run {run_id}")

    envelope = build_event(payload)
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    secret = org["webhook_secret"]

    def sign(b: bytes) -> Mapping[str, str]:
        return {"Ashby-Signature": ashby_sign(secret, b)}

    status, text = await deliver(url=ashby_webhook_url, body=body, sign=sign)
    await mark_emitted(pool, event_id, status=status, attempt_at=datetime.now(timezone.utc))
    log.info("ashby_event_emitted", event_id=str(event_id),
             action=envelope["action"], status=status)
    return status, text
