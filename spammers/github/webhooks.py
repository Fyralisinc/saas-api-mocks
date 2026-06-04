"""Outbound GitHub webhook delivery.

When the orchestrator drains a live ``github.*`` timeline event, this module
builds the matching webhook payload, signs it (HMAC-SHA1 + HMAC-SHA256), and
POSTs it to the consumer's ``/webhooks/github`` with GitHub's headers:

  X-GitHub-Event, X-GitHub-Delivery, X-GitHub-Hook-ID,
  X-Hub-Signature (SHA-1), X-Hub-Signature-256 (SHA-256),
  X-GitHub-Hook-Installation-Target-Type / -ID, User-Agent: GitHub-Hookshot/…

Covers the full live signal set GitHub delivers to a GitHub App: the content
events (pull_request, issues, push, pull_request_review, issue_comment,
check_run) and the App-level lifecycle events (installation,
installation_repositories, ping). Each builder returns a complete envelope, so
push and lifecycle — whose shapes differ from the ``{action, <resource>}``
content shape — are first-class rather than special cases bolted on.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Mapping
from uuid import UUID

import asyncpg
import structlog

from spammers.common.ids import github_delivery_id, github_hook_id
from spammers.common.signing import github_sign, github_sign_sha1
from spammers.common.webhook_emitter import deliver, mark_emitted
from spammers.github.dto import (
    check_run_dto,
    installation_dto,
    iso,
    issue_comment_dto,
    issue_dto,
    min_repo_dto,
    pull_request_dto,
    push_commit_file_lists,
    repo_dto,
    review_dto,
    synth_commit_files,
    user_dto,
)

log = structlog.get_logger("spammers.github.webhooks")

_ZERO_SHA = "0" * 40

# timeline.events type -> GitHub X-GitHub-Event name
_EVENT_NAME = {
    "github.pull_request": "pull_request",
    "github.issues": "issues",
    "github.pull_request_review": "pull_request_review",
    "github.issue_comment": "issue_comment",
    "github.check_run": "check_run",
    "github.push": "push",
    "github.installation": "installation",
    "github.installation_repositories": "installation_repositories",
    "github.ping": "ping",
}

_LIFECYCLE_TYPES = {"github.installation", "github.installation_repositories", "github.ping"}


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

    # App + its installation (one per run) — needed by every payload class.
    app = await pool.fetchrow(
        """
        SELECT a.app_id, a.webhook_secret,
               inst.id AS inst_pk, inst.installation_id, inst.account_login,
               inst.account_type, inst.account_id, inst.repository_selection,
               inst.created_at, inst.suspended_at
          FROM app_github.apps a
          JOIN app_github.installations inst ON inst.app_pk = a.id
         WHERE a.run_id = $1
         LIMIT 1
        """,
        run_id,
    )
    if app is None:
        raise LookupError(f"no github app for run {run_id}")
    app = dict(app)

    if etype == "github.push":
        envelope = await _build_push(pool, run_id, payload, app)
    elif etype == "github.installation":
        envelope = await _build_installation(pool, payload, app)
    elif etype == "github.installation_repositories":
        envelope = await _build_installation_repos(pool, payload, app)
    elif etype == "github.ping":
        envelope = _build_ping(app, github_events_url)
    else:
        envelope = await _build_content(pool, run_id, etype, payload, app)

    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    secret = app["webhook_secret"]

    def sign(b: bytes) -> Mapping[str, str]:
        # Real GitHub sends both signatures on every delivery with a secret set.
        return {
            "X-Hub-Signature": github_sign_sha1(secret, b),
            "X-Hub-Signature-256": github_sign(secret, b),
        }

    app_id = app["app_id"]
    extra_headers = {
        "User-Agent": f"GitHub-Hookshot/{int(app_id) & 0xFFFFFFF:07x}",
        "X-GitHub-Event": event_name,
        "X-GitHub-Delivery": github_delivery_id(),
        "X-GitHub-Hook-ID": str(github_hook_id(app_id)),
        "X-GitHub-Hook-Installation-Target-Type": "integration",
        "X-GitHub-Hook-Installation-Target-ID": str(app_id),
    }
    status, text = await deliver(url=github_events_url, body=body, sign=sign, extra_headers=extra_headers)
    await mark_emitted(pool, event_id, status=status, attempt_at=datetime.now(timezone.utc))
    log.info("github_event_emitted", event_id=str(event_id), gh_event=event_name, status=status)
    return status, text


async def _repo_row(pool, run_id: UUID, full: str) -> dict:
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
    return dict(repo_row)


def _inst_view(app: dict) -> dict:
    """The installation row keys ``installation_dto`` expects."""
    return {
        k: app[k]
        for k in ("installation_id", "account_login", "account_type", "account_id",
                  "repository_selection", "created_at", "suspended_at")
    }


# ----------------------------- content events -----------------------------

async def _build_content(pool, run_id: UUID, etype: str, payload: dict, app: dict) -> dict:
    """Envelope for the ``{action, <resource>}`` content events."""
    full = payload["repo"]
    repo_row = await _repo_row(pool, run_id, full)
    repo_pk = repo_row["id"]
    action = payload.get("action", "")

    if etype == "github.pull_request":
        pr = await pool.fetchrow(
            "SELECT * FROM app_github.pull_requests WHERE repo_pk = $1 AND number = $2",
            repo_pk, payload["number"],
        )
        inner = {"action": action, "number": pr["number"],
                 "pull_request": pull_request_dto(dict(pr), full, repo_row)}
        sender = pr["user_login"]
    elif etype == "github.issues":
        issue = await pool.fetchrow(
            "SELECT * FROM app_github.issues WHERE repo_pk = $1 AND number = $2",
            repo_pk, payload["number"],
        )
        inner = {"action": action, "issue": issue_dto(dict(issue), full)}
        sender = issue["user_login"]
    elif etype == "github.pull_request_review":
        pr = await pool.fetchrow(
            "SELECT * FROM app_github.pull_requests WHERE repo_pk = $1 AND number = $2",
            repo_pk, payload["number"],
        )
        rv = await pool.fetchrow(
            "SELECT * FROM app_github.reviews WHERE pr_pk = $1 ORDER BY submitted_at DESC LIMIT 1",
            pr["id"],
        )
        inner = {"action": action, "review": review_dto(dict(rv)),
                 "pull_request": pull_request_dto(dict(pr), full, repo_row)}
        sender = rv["user_login"]
    elif etype == "github.issue_comment":
        comment = await pool.fetchrow(
            "SELECT * FROM app_github.issue_comments WHERE repo_pk = $1 AND issue_number = $2 "
            "ORDER BY created_at DESC LIMIT 1",
            repo_pk, payload["issue_number"],
        )
        issue = await pool.fetchrow(
            "SELECT * FROM app_github.issues WHERE repo_pk = $1 AND number = $2",
            repo_pk, payload["issue_number"],
        )
        inner = {"action": action, "comment": issue_comment_dto(dict(comment), full),
                 "issue": issue_dto(dict(issue), full) if issue else None}
        sender = comment["user_login"]
    elif etype == "github.check_run":
        cr = await pool.fetchrow(
            "SELECT * FROM app_github.check_runs WHERE repo_pk = $1 AND head_sha = $2 "
            "ORDER BY started_at DESC LIMIT 1",
            repo_pk, payload["head_sha"],
        )
        inner = {"action": action, "check_run": check_run_dto(dict(cr))}
        sender = repo_row["owner"]  # check runs are bot-originated
    else:
        raise LookupError(f"unhandled github content event type: {etype}")

    inner["repository"] = repo_dto(repo_row)
    inner["sender"] = user_dto(sender)
    inner["installation"] = {"id": app["installation_id"]}
    return inner


# --------------------------------- push -----------------------------------

async def _build_push(pool, run_id: UUID, payload: dict, app: dict) -> dict:
    full = payload["repo"]
    repo_row = await _repo_row(pool, run_id, full)
    after = payload["after"]
    before = payload.get("before", _ZERO_SHA)
    c = await pool.fetchrow(
        "SELECT * FROM app_github.commits WHERE repo_pk = $1 AND sha = $2", repo_row["id"], after
    )
    if c is None:
        raise LookupError(f"push commit not provisioned: {full}@{after}")
    c = dict(c)
    when = iso(c["committed_at"])
    # The push commit shape differs from the REST commit shape: ids are bare
    # SHAs and the changed-file paths live on each commit (head_commit drives the
    # consumer's blast-radius layer). Same (repo, sha) → same files as the
    # single-commit GET, so a backfilled commit and its live push agree.
    added, removed, modified = push_commit_file_lists(synth_commit_files(full, after))
    commit_obj = {
        "id": after,
        "tree_id": after,
        "distinct": True,
        "message": c["message"],
        "timestamp": when,
        "url": f"https://github.com/{full}/commit/{after}",
        "author": {"name": c["author_login"], "email": c["author_email"], "username": c["author_login"]},
        "committer": {"name": c["author_login"], "email": c["author_email"], "username": c["author_login"]},
        "added": added,
        "removed": removed,
        "modified": modified,
    }
    return {
        "ref": payload["ref"],
        "before": before,
        "after": after,
        "created": before == _ZERO_SHA,
        "deleted": False,
        "forced": False,
        "base_ref": None,
        "compare": f"https://github.com/{full}/compare/{before[:12]}...{after[:12]}",
        "commits": [commit_obj],
        "head_commit": commit_obj,
        "repository": repo_dto(repo_row),
        "pusher": {"name": c["author_login"], "email": c["author_email"]},
        "sender": user_dto(c["author_login"]),
        "installation": {"id": app["installation_id"]},
    }


# ------------------------------- lifecycle --------------------------------

async def _build_installation(pool, payload: dict, app: dict) -> dict:
    repos = await pool.fetch(
        "SELECT * FROM app_github.repositories WHERE installation_pk = $1 ORDER BY name",
        app["inst_pk"],
    )
    return {
        "action": payload.get("action", "created"),
        "installation": installation_dto(_inst_view(app), app["app_id"]),
        "repositories": [min_repo_dto(dict(r)) for r in repos],
        "sender": user_dto(app["account_login"]),
    }


async def _build_installation_repos(pool, payload: dict, app: dict) -> dict:
    action = payload.get("action", "removed")
    names = payload.get("repos", []) or []
    rows = []
    if names:
        rows = await pool.fetch(
            "SELECT * FROM app_github.repositories WHERE installation_pk = $1 AND name = ANY($2::text[])",
            app["inst_pk"], names,
        )
    minimal = [min_repo_dto(dict(r)) for r in rows]
    return {
        "action": action,
        "installation": installation_dto(_inst_view(app), app["app_id"]),
        "repository_selection": app["repository_selection"],
        "repositories_added": minimal if action == "added" else [],
        "repositories_removed": minimal if action == "removed" else [],
        "sender": user_dto(app["account_login"]),
    }


def _build_ping(app: dict, events_url: str) -> dict:
    app_id = app["app_id"]
    hook_id = github_hook_id(app_id)
    stamp = iso(datetime(2024, 1, 1, tzinfo=timezone.utc))
    return {
        "zen": "Non-blocking is better than blocking.",
        "hook_id": hook_id,
        "hook": {
            "type": "App",
            "id": hook_id,
            "name": "web",
            "active": True,
            "events": ["push", "pull_request", "issues", "issue_comment",
                       "pull_request_review", "check_run"],
            "config": {"content_type": "json", "insecure_ssl": "0", "url": events_url},
            "updated_at": stamp,
            "created_at": stamp,
            "app_id": app_id,
        },
        "app_id": app_id,
        "sender": user_dto(app["account_login"]),
    }
