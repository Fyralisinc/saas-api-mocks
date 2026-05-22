"""Outbound GitHub webhook delivery.

When the orchestrator drains a live ``github.*`` timeline event, this module
builds the matching webhook payload, signs it (HMAC-SHA256), and POSTs it to the
consumer's ``/webhooks/github`` with GitHub's headers:

  X-GitHub-Event, X-GitHub-Delivery, X-Hub-Signature-256,
  X-GitHub-Hook-Installation-Target-Type / -ID
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Mapping
from uuid import UUID

import asyncpg
import structlog

from spammers.common.ids import github_delivery_id
from spammers.common.signing import github_sign
from spammers.common.webhook_emitter import deliver, mark_emitted
from spammers.github.dto import (
    check_run_dto,
    issue_comment_dto,
    issue_dto,
    pull_request_dto,
    repo_dto,
    review_dto,
)

log = structlog.get_logger("spammers.github.webhooks")

# timeline.events type -> GitHub X-GitHub-Event name
_EVENT_NAME = {
    "github.pull_request": "pull_request",
    "github.issues": "issues",
    "github.pull_request_review": "pull_request_review",
    "github.issue_comment": "issue_comment",
    "github.check_run": "check_run",
}


async def emit_event(
    pool: asyncpg.Pool,
    *,
    run_id: UUID,
    event_id: UUID,
    github_events_url: str,
) -> tuple[int, str]:
    """Fetch a ``github.*`` timeline event and POST its webhook to the consumer."""
    ev = await pool.fetchrow(
        "SELECT type, payload, virtual_ts FROM timeline.events WHERE id = $1", event_id
    )
    if ev is None:
        raise LookupError(f"github timeline event not found: {event_id}")
    etype = ev["type"]
    event_name = _EVENT_NAME.get(etype)
    if event_name is None:
        raise LookupError(f"no github webhook mapping for event type: {etype}")
    payload = ev["payload"] if isinstance(ev["payload"], dict) else json.loads(ev["payload"])

    app = await pool.fetchrow(
        """
        SELECT a.app_id, a.webhook_secret, inst.installation_id, inst.account_login
          FROM app_github.apps a
          JOIN app_github.installations inst ON inst.app_pk = a.id
         WHERE a.run_id = $1
         LIMIT 1
        """,
        run_id,
    )
    if app is None:
        raise LookupError(f"no github app for run {run_id}")

    full = payload["repo"]
    owner, name = full.split("/", 1)
    repo_row = await pool.fetchrow(
        """
        SELECT r.* FROM app_github.repositories r
          JOIN app_github.installations inst ON inst.id = r.installation_pk
          JOIN app_github.apps a ON a.id = inst.app_pk
         WHERE a.run_id = $1 AND r.owner = $2 AND r.name = $3
        """,
        run_id, owner, name,
    )
    if repo_row is None:
        raise LookupError(f"repo not provisioned for github event: {full}")

    envelope, sender_login = await _build_payload(pool, etype, payload, dict(repo_row), full)
    envelope["repository"] = repo_dto(dict(repo_row))
    envelope["sender"] = {"login": sender_login, "type": "User"}
    envelope["installation"] = {"id": app["installation_id"]}

    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    secret = app["webhook_secret"]

    def sign(b: bytes) -> Mapping[str, str]:
        return {"X-Hub-Signature-256": github_sign(secret, b)}

    extra_headers = {
        "X-GitHub-Event": event_name,
        "X-GitHub-Delivery": github_delivery_id(),
        "X-GitHub-Hook-Installation-Target-Type": "integration",
        "X-GitHub-Hook-Installation-Target-ID": str(app["app_id"]),
    }
    status, text = await deliver(url=github_events_url, body=body, sign=sign, extra_headers=extra_headers)
    await mark_emitted(pool, event_id, status=status, attempt_at=datetime.now(timezone.utc))
    log.info("github_event_emitted", event_id=str(event_id), gh_event=event_name, status=status)
    return status, text


async def _build_payload(pool, etype: str, payload: dict, repo_row: dict, full: str):
    """Return ``(envelope_without_repo/sender/installation, sender_login)``."""
    repo_pk = repo_row["id"]
    action = payload.get("action", "")

    if etype == "github.pull_request":
        pr = await pool.fetchrow(
            "SELECT * FROM app_github.pull_requests WHERE repo_pk = $1 AND number = $2",
            repo_pk, payload["number"],
        )
        return {"action": action, "number": pr["number"],
                "pull_request": pull_request_dto(dict(pr), full)}, pr["user_login"]

    if etype == "github.issues":
        issue = await pool.fetchrow(
            "SELECT * FROM app_github.issues WHERE repo_pk = $1 AND number = $2",
            repo_pk, payload["number"],
        )
        return {"action": action, "issue": issue_dto(dict(issue), full)}, issue["user_login"]

    if etype == "github.pull_request_review":
        pr = await pool.fetchrow(
            "SELECT * FROM app_github.pull_requests WHERE repo_pk = $1 AND number = $2",
            repo_pk, payload["number"],
        )
        rv = await pool.fetchrow(
            "SELECT * FROM app_github.reviews WHERE pr_pk = $1 ORDER BY submitted_at DESC LIMIT 1",
            pr["id"],
        )
        return {"action": action, "review": review_dto(dict(rv)),
                "pull_request": pull_request_dto(dict(pr), full)}, rv["user_login"]

    if etype == "github.issue_comment":
        comment = await pool.fetchrow(
            """
            SELECT * FROM app_github.issue_comments
             WHERE repo_pk = $1 AND issue_number = $2 ORDER BY created_at DESC LIMIT 1
            """,
            repo_pk, payload["issue_number"],
        )
        issue = await pool.fetchrow(
            "SELECT * FROM app_github.issues WHERE repo_pk = $1 AND number = $2",
            repo_pk, payload["issue_number"],
        )
        return {"action": action, "comment": issue_comment_dto(dict(comment), full),
                "issue": issue_dto(dict(issue), full) if issue else None}, comment["user_login"]

    if etype == "github.check_run":
        cr = await pool.fetchrow(
            "SELECT * FROM app_github.check_runs WHERE repo_pk = $1 AND head_sha = $2 ORDER BY started_at DESC LIMIT 1",
            repo_pk, payload["head_sha"],
        )
        return {"action": action, "check_run": check_run_dto(dict(cr))}, repo_row["owner"]

    raise LookupError(f"unhandled github event type: {etype}")
