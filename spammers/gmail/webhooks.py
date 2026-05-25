"""Outbound Gmail Pub/Sub push delivery.

When the orchestrator drains a live ``gmail.message`` timeline event, this builds
the Pub/Sub push envelope (data = base64 of ``{emailAddress, historyId}``), signs
an OIDC JWT (RS256, verifiable against the mock's ``/jwks``), and POSTs it with
``Authorization: Bearer <jwt>`` to the consumer's push endpoint — the consumer
then drains ``users.history.list`` from the bookmark.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Mapping
from uuid import UUID

import asyncpg
import structlog

from spammers.common.webhook_emitter import deliver, mark_emitted
from spammers.gmail.push import build_envelope, sign_oidc

log = structlog.get_logger("spammers.gmail.webhooks")


async def emit_event(
    pool: asyncpg.Pool,
    *,
    run_id: UUID,
    event_id: UUID,
    gmail_pubsub_url: str,
) -> tuple[int, str]:
    """Fetch a ``gmail.message`` event and POST the signed Pub/Sub push."""
    ev = await pool.fetchrow(
        "SELECT payload FROM timeline.events WHERE id = $1", event_id)
    if ev is None:
        raise LookupError(f"gmail timeline event not found: {event_id}")
    payload = ev["payload"] if isinstance(ev["payload"], dict) else json.loads(ev["payload"])
    email = payload.get("email")
    history_id = int(payload.get("history_id") or 0)
    if not email:
        raise LookupError(f"gmail event {event_id} has no email")

    cust = await pool.fetchrow(
        """SELECT pubsub_oidc_private_key, pubsub_oidc_public_key, pubsub_audience,
                  service_account_email FROM app_gmail.customers WHERE run_id = $1""",
        run_id)
    if cust is None:
        raise LookupError(f"no gmail customer for run {run_id}")

    tenant = str(run_id).replace("-", "")
    subscription = f"projects/spammer-ingest/subscriptions/gmail-{tenant}-sub"
    body = json.dumps(build_envelope(email, history_id, subscription),
                      separators=(",", ":")).encode("utf-8")
    jwt_tok = sign_oidc(
        cust["pubsub_oidc_private_key"], cust["pubsub_oidc_public_key"],
        audience=cust["pubsub_audience"], push_sa_email=cust["service_account_email"])

    def sign(_b: bytes) -> Mapping[str, str]:
        return {"Authorization": f"Bearer {jwt_tok}"}

    status, text = await deliver(url=gmail_pubsub_url, body=body, sign=sign)
    await mark_emitted(pool, event_id, status=status, attempt_at=datetime.now(timezone.utc))
    log.info("gmail_push_emitted", event_id=str(event_id), email=email,
             history_id=history_id, status=status)
    return status, text
