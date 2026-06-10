"""Outbound Figma Webhooks-v2 delivery (the body-PASSCODE scheme — NO HMAC).

When a watched Figma file changes, Figma POSTs a **Webhooks v2** delivery whose
authenticity is a **plaintext ``passcode`` carried as a top-level JSON field in the
body** — there is **NO signature header and NO HMAC** (the entire security model,
per developers.figma.com/docs/rest-api/webhooks-security: "compare the ``passcode``
we pass back to you … with the ``passcode`` originally provided … a wrong passcode
→ respond 400"). This is the one place Figma genuinely differs from every HMAC
provider (slack/github/jira/deel/hibob/…).

Every delivery shares the base envelope ``{event_type, passcode, timestamp,
webhook_id}``; concrete events add their own fields. The two ingestion-relevant
events:

  FILE_VERSION_UPDATE  + {created_at, description?, file_key, file_name,
                          triggered_by:User, version_id}     (NO `label` — spec)
  FILE_COMMENT         + {comment:[CommentFragment], comment_id, created_at,
                          file_key, file_name, mentions?, triggered_by:User}

``triggered_by`` is a FULL User ``{id, handle, img_url}``. A ``PING`` carries only
the base envelope and is NOT an observation (the consumer rejects it).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Mapping
from uuid import UUID

import asyncpg
import structlog

from spammers.common.webhook_emitter import deliver, mark_emitted
from spammers.figma import dto as _dto

log = structlog.get_logger("spammers.figma.webhooks")


def build_event(payload: dict, *, passcode: str, webhook_id: str,
                occurred: datetime) -> dict:
    """Assemble a Webhooks-v2 delivery body from a thin ``figma.event`` payload.

    The base envelope carries the plaintext ``passcode`` (Figma's whole auth model);
    the event-specific fields ride on the ``event`` sub-dict the inject already shaped.
    """
    base = {
        "event_type": payload.get("event_type", "FILE_VERSION_UPDATE"),
        "passcode": passcode,
        "timestamp": _dto.iso_z(occurred),
        "webhook_id": webhook_id,
    }
    if base["event_type"] == "PING":
        return base
    event = payload.get("event")
    if isinstance(event, dict):
        base.update(event)
    return base


async def emit_event(
    pool: asyncpg.Pool,
    *,
    run_id: UUID,
    event_id: UUID,
    figma_webhook_url: str,
) -> tuple[int, str]:
    """Fetch a ``figma.event`` timeline event and POST its passcode-in-body delivery.

    Figma signs NOTHING — the passcode lives in the body, so the delivery carries no
    signature header (``sign`` returns no headers)."""
    ev = await pool.fetchrow(
        "SELECT payload, virtual_ts FROM timeline.events WHERE id = $1", event_id)
    if ev is None:
        raise LookupError(f"figma timeline event not found: {event_id}")
    payload = ev["payload"] if isinstance(ev["payload"], dict) else json.loads(ev["payload"])

    team = await pool.fetchrow(
        "SELECT team_id, webhook_passcode, webhook_id FROM app_figma.teams WHERE run_id = $1",
        run_id)
    if team is None:
        raise LookupError(f"no figma team for run {run_id}")

    occurred = ev["virtual_ts"] or datetime.now(timezone.utc)
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=timezone.utc)
    envelope = build_event(payload, passcode=team["webhook_passcode"],
                           webhook_id=team["webhook_id"], occurred=occurred)
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")

    def sign(_b: bytes) -> Mapping[str, str]:
        # Figma webhooks have NO signature header — auth is the body passcode.
        return {}

    status, text = await deliver(url=figma_webhook_url, body=body, sign=sign)
    await mark_emitted(pool, event_id, status=status, attempt_at=datetime.now(timezone.utc))
    log.info("figma_event_emitted", event_id=str(event_id),
             event_type=envelope["event_type"], status=status)
    return status, text
