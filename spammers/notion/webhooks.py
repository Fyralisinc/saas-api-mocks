"""Outbound Notion webhook delivery.

When the orchestrator drains a live ``notion.page`` timeline event, this builds
the *thin* event Notion sends (no page body — just the entity ref), signs it
``X-Notion-Signature: sha256=HMAC-SHA256(verification_token, raw_body)``, and
POSTs it to the consumer's Notion webhook. The consumer then hydrates the page
via ``GET /v1/pages/{id}`` — so the dedup collapses with any backfill copy.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Mapping
from uuid import UUID

import asyncpg
import structlog

from spammers.common.signing import github_sign  # returns "sha256=<hex>"
from spammers.common.webhook_emitter import deliver, mark_emitted

log = structlog.get_logger("spammers.notion.webhooks")


async def emit_event(
    pool: asyncpg.Pool,
    *,
    run_id: UUID,
    event_id: UUID,
    notion_webhook_url: str,
) -> tuple[int, str]:
    """Fetch a ``notion.page`` timeline event and POST its signed thin webhook."""
    ev = await pool.fetchrow(
        "SELECT type, payload, virtual_ts FROM timeline.events WHERE id = $1", event_id)
    if ev is None:
        raise LookupError(f"notion timeline event not found: {event_id}")
    payload = ev["payload"] if isinstance(ev["payload"], dict) else json.loads(ev["payload"])
    page_id = payload.get("page_id")
    if not page_id:
        raise LookupError(f"notion event {event_id} has no page_id")

    integ = await pool.fetchrow(
        "SELECT workspace_id, verification_token FROM app_notion.integrations WHERE run_id = $1",
        run_id)
    if integ is None:
        raise LookupError(f"no notion integration for run {run_id}")

    event_type = payload.get("event_type") or "page.content_updated"
    envelope = {
        "id": str(__import__("uuid").uuid4()),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "workspace_id": integ["workspace_id"],
        "type": event_type,
        "entity": {"id": page_id, "type": "page"},
    }
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    secret = integ["verification_token"]

    def sign(b: bytes) -> Mapping[str, str]:
        return {"X-Notion-Signature": github_sign(secret, b)}

    status, text = await deliver(
        url=notion_webhook_url, body=body, sign=sign,
        extra_headers={"X-Notion-Request-Id": secrets.token_hex(8)},
    )
    await mark_emitted(pool, event_id, status=status, attempt_at=datetime.now(timezone.utc))
    log.info("notion_event_emitted", event_id=str(event_id), type=event_type, status=status)
    return status, text
