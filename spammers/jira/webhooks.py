"""Outbound Jira webhook delivery.

When the orchestrator drains a live ``jira.issue`` timeline event, this builds
the Jira Cloud webhook body (``webhookEvent: "jira:issue_updated"`` with the
status-transition ``changelog``), signs it ``X-Hub-Signature: sha256=HMAC-SHA256(
webhook_secret, raw_body)`` — the real scheme the Fyralis verifier
(``services/webhooks/signatures/jira.py``) enforces (body-only HMAC, GitHub
scheme, un-suffixed header) — and POSTs it to the consumer's webhook receiver.

The issue's ``self`` host matches the backfill site namespace, so a
webhook-delivered transition dedups against its backfill/poll twin
(``external_id = jira:{site}:transition:{issue_id}:{history_id}``).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Mapping
from uuid import UUID

import asyncpg
import structlog

from spammers.common.signing import github_sign  # returns "sha256=<hex>"
from spammers.common.webhook_emitter import deliver, mark_emitted
from spammers.jira.dto import jira_ts

log = structlog.get_logger("spammers.jira.webhooks")


async def emit_event(
    pool: asyncpg.Pool,
    *,
    run_id: UUID,
    event_id: UUID,
    jira_webhook_url: str,
) -> tuple[int, str]:
    """Fetch a ``jira.issue`` timeline event and POST its signed webhook."""
    ev = await pool.fetchrow(
        "SELECT payload FROM timeline.events WHERE id = $1", event_id)
    if ev is None:
        raise LookupError(f"jira timeline event not found: {event_id}")
    payload = ev["payload"] if isinstance(ev["payload"], dict) else json.loads(ev["payload"])
    issue_id = payload.get("issue_id")
    if not issue_id:
        raise LookupError(f"jira event {event_id} has no issue_id")

    inst = await pool.fetchrow(
        "SELECT id, base_url, webhook_secret FROM app_jira.installations WHERE run_id = $1", run_id)
    if inst is None:
        raise LookupError(f"no jira installation for run {run_id}")
    issue = await pool.fetchrow(
        "SELECT i.*, p.project_id, p.key AS project_key, p.name AS project_name "
        "FROM app_jira.issues i JOIN app_jira.projects p ON p.id=i.project_pk "
        "WHERE i.installation_pk=$1 AND i.issue_id=$2",
        inst["id"], str(issue_id))
    if issue is None:
        raise LookupError(f"jira issue {issue_id} not found")

    base_url = inst["base_url"]
    history = None
    history_id = payload.get("history_id")
    if history_id:
        history = await pool.fetchrow(
            "SELECT * FROM app_jira.changelogs WHERE issue_pk=$1 AND history_id=$2",
            issue["id"], str(history_id))

    actor = {"accountId": issue["reporter_account_id"]} if issue["reporter_account_id"] else None
    issue_obj = {
        "id": str(issue["issue_id"]),
        "key": issue["issue_key"],
        "self": f"{base_url}/rest/api/3/issue/{issue['issue_id']}",
        "fields": {
            "summary": issue["summary"],
            "status": {"name": issue["status"]},
            "updated": jira_ts(issue["updated_at"]),
            "project": {"id": issue["project_id"], "key": issue["project_key"],
                        "name": issue["project_name"]},
        },
    }
    envelope = {
        "timestamp": int(time.time() * 1000),
        "webhookEvent": "jira:issue_updated",
        "issue_event_type_name": "issue_generic",
        "user": actor,
        "issue": issue_obj,
    }
    if history is not None:
        items = history["items"]
        if isinstance(items, str):
            items = json.loads(items)
        envelope["changelog"] = {"id": history["history_id"], "items": items}

    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    secret = inst["webhook_secret"]

    def sign(b: bytes) -> Mapping[str, str]:
        return {"X-Hub-Signature": github_sign(secret, b)}

    status, text = await deliver(url=jira_webhook_url, body=body, sign=sign)
    await mark_emitted(pool, event_id, status=status, attempt_at=datetime.now(timezone.utc))
    log.info("jira_event_emitted", event_id=str(event_id), issue=issue["issue_key"], status=status)
    return status, text
