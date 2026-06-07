"""Outbound Notion webhook delivery.

When the orchestrator drains a live ``notion.page`` timeline event, this builds
the *thin* event Notion sends (no page body — just the entity ref), signs it
``X-Notion-Signature: sha256=HMAC-SHA256(verification_token, raw_body)``, and
POSTs it to the consumer's Notion webhook. The consumer then hydrates the page
via ``GET /v1/pages/{id}`` — so the dedup collapses with any backfill copy.

The first delivery a subscription ever receives is the unsigned one-time
verification handshake (``emit_verification``) — body ``{"verification_token":…}``
— which the operator copies into the consumer's config.
"""
from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timezone
from typing import Mapping
from uuid import UUID

import asyncpg
import structlog

from spammers.common.signing import github_sign  # returns "sha256=<hex>"
from spammers.common.webhook_emitter import deliver, mark_emitted

log = structlog.get_logger("spammers.notion.webhooks")

_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # stable derivation namespace


def _parent_ref(parent_type: str | None, parent_id: str | None, workspace_id: str) -> dict:
    if parent_type == "database_id":
        return {"id": parent_id, "type": "database"}
    if parent_type == "page_id":
        return {"id": parent_id, "type": "page"}
    return {"id": workspace_id, "type": "workspace"}


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
        "SELECT id, workspace_id, workspace_name, bot_user_id, verification_token "
        "FROM app_notion.integrations WHERE run_id = $1", run_id)
    if integ is None:
        raise LookupError(f"no notion integration for run {run_id}")
    workspace_id = integ["workspace_id"]

    # `data` carries the entity's parent ref (the page may already be gone for a
    # delete event — then we just omit it).
    pg = await pool.fetchrow(
        "SELECT parent_type, parent_id FROM app_notion.pages WHERE integration_pk=$1 AND page_id=$2",
        integ["id"], page_id)
    data: dict = {}
    if pg is not None:
        data["parent"] = _parent_ref(pg["parent_type"], pg["parent_id"], workspace_id)

    event_type = payload.get("event_type") or "page.content_updated"
    envelope = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "workspace_id": workspace_id,
        "workspace_name": integ["workspace_name"],
        "subscription_id": str(uuid.uuid5(_NS, workspace_id + ":subscription")),
        "integration_id": str(uuid.uuid5(_NS, workspace_id + ":integration")),
        "type": event_type,
        "authors": [{"id": integ["bot_user_id"], "type": "bot"}],
        "attempt_number": 1,
        "entity": {"id": page_id, "type": "page"},
        "data": data,
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


async def emit_verification(
    pool: asyncpg.Pool,
    *,
    run_id: UUID,
    notion_webhook_url: str,
) -> tuple[int, str]:
    """Send Notion's one-time subscription-verification handshake: an UNSIGNED
    POST whose body is ``{"verification_token": "secret_…"}``. The token *is* the
    secret later events are signed with, so there is nothing to verify against
    yet — the consumer captures it out-of-band. Returns ``(status, text)``."""
    integ = await pool.fetchrow(
        "SELECT verification_token FROM app_notion.integrations WHERE run_id = $1", run_id)
    if integ is None:
        raise LookupError(f"no notion integration for run {run_id}")
    body = json.dumps({"verification_token": integ["verification_token"]},
                      separators=(",", ":")).encode("utf-8")

    def _no_sig(_b: bytes) -> Mapping[str, str]:
        return {}

    status, text = await deliver(
        url=notion_webhook_url, body=body, sign=_no_sig,
        extra_headers={"X-Notion-Request-Id": secrets.token_hex(8)},
    )
    log.info("notion_verification_sent", run_id=str(run_id), status=status)
    return status, text
