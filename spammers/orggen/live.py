"""Live event injection helpers.

One-off injection of forward-dated (is_historical=FALSE) events into each
provider's tables, so the emission loop picks them up and delivers them as
signed webhooks. Used by the ``inject`` CLI subcommand and the Studio
control panel.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

import asyncpg
import structlog

from spammers.common.clock import get_clock
from spammers.common.ids import (
    drive_file_id, gcal_event_id, gcal_ical_uid, github_sha,
    gmail_message_id, gmail_thread_id, notion_id, slack_user_token,
)


_DM_USER_SCOPES = ["im:read", "im:history", "mpim:read", "mpim:history", "users:read"]


async def provision_slack_user_tokens(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    handles: Optional[list[str]] = None,
) -> dict[str, str]:
    """Mint per-user xoxp tokens for DM ingestion (the consent rows).

    Mirrors the doc's per-user DM control plane: each consenting human gets an
    xoxp user token scoped to im/mpim. With ``handles=None`` we enrol every user
    who participates in any 1:1 or group DM (membership or message authorship).
    Returns ``{slack_user_id: xoxp_token}``. Idempotent per (workspace, user).

    Fyralis normally obtains these via OAuth (authed_user.access_token); this is
    a convenience for frozen Director runs that want DM coverage without driving
    a per-user OAuth dance.
    """
    if handles:
        rows = await pool.fetch(
            """
            SELECT u.id AS user_pk, u.workspace_id, u.slack_user_id
              FROM app_slack.users u
              JOIN app_slack.workspaces w ON w.id = u.workspace_id
              JOIN org.people p ON p.id = u.person_id
             WHERE w.run_id = $1 AND p.handle = ANY($2::text[])
            """,
            run_id, handles,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT DISTINCT u.id AS user_pk, u.workspace_id, u.slack_user_id
              FROM app_slack.users u
              JOIN app_slack.workspaces w ON w.id = u.workspace_id
             WHERE w.run_id = $1
               AND u.id IN (
                     SELECT cm.user_pk
                       FROM app_slack.channel_membership cm
                       JOIN app_slack.channels c ON c.id = cm.channel_pk
                      WHERE c.is_im OR c.is_mpim
                     UNION
                     SELECT m.user_pk
                       FROM app_slack.messages m
                       JOIN app_slack.channels c ON c.id = m.channel_pk
                      WHERE (c.is_im OR c.is_mpim) AND m.user_pk IS NOT NULL
                   )
            """,
            run_id,
        )
    out: dict[str, str] = {}
    for r in rows:
        token = slack_user_token()
        await pool.execute(
            """
            INSERT INTO app_slack.user_tokens (id, workspace_id, slack_user_id, user_token, scopes)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            ON CONFLICT (workspace_id, slack_user_id) DO UPDATE
              SET user_token = EXCLUDED.user_token, scopes = EXCLUDED.scopes, revoked_at = NULL
            """,
            uuid4(), r["workspace_id"], r["slack_user_id"], token, json.dumps(_DM_USER_SCOPES),
        )
        out[r["slack_user_id"]] = token
    log.info("slack_user_tokens_provisioned", run_id=str(run_id), count=len(out))
    return out


log = structlog.get_logger("spammers.orggen.live")


async def inject_slack_message(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    handle: Optional[str] = None,
    channel: Optional[str] = None,
    text: Optional[str] = None,
    at_virtual: Optional[datetime] = None,
) -> UUID:
    """Append one ``slack.message`` event to the timeline as not-historical.

    Defaults: random person, #general, a banter line, virtual_now + 1s.
    Returns the new event id.
    """
    if handle is None:
        row = await pool.fetchrow(
            "SELECT id, handle FROM org.people WHERE run_id = $1 ORDER BY random() LIMIT 1",
            run_id,
        )
    else:
        row = await pool.fetchrow(
            "SELECT id, handle FROM org.people WHERE run_id = $1 AND handle = $2",
            run_id, handle,
        )
    if row is None:
        raise LookupError("no people in this run; did you forget `prepare`?")
    actor_id = row["id"]

    clock = await get_clock(pool, run_id)
    when = at_virtual or (clock.virtual_now + timedelta(seconds=1))
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    if text is None:
        text = f"[live] hello from {row['handle']} @ {when.isoformat()}"
    if channel is None:
        channel = "#general"

    event_id = uuid4()
    await pool.execute(
        """
        INSERT INTO timeline.events
            (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical)
        VALUES ($1, $2, $3, 'slack.message', $4, $5::jsonb, '{}'::jsonb, FALSE)
        """,
        event_id, run_id, when, actor_id,
        json.dumps({"channel": channel, "text": text, "kind": "live"}),
    )
    return event_id


async def inject_discord_message(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    handle: Optional[str] = None,
    channel: Optional[str] = None,
    text: Optional[str] = None,
    at_virtual: Optional[datetime] = None,
) -> UUID:
    """Append one ``discord.message`` event (not-historical) to the timeline.

    The Discord mock's GatewayDispatcher picks it up, projects it into
    ``app_discord.messages``, and pushes a ``MESSAGE_CREATE`` to connected bots.
    Defaults: random person, ``general`` channel, virtual_now + 1s.
    """
    if handle is None:
        row = await pool.fetchrow(
            "SELECT id, handle FROM org.people WHERE run_id = $1 ORDER BY random() LIMIT 1",
            run_id,
        )
    else:
        row = await pool.fetchrow(
            "SELECT id, handle FROM org.people WHERE run_id = $1 AND handle = $2",
            run_id, handle,
        )
    if row is None:
        raise LookupError("no people in this run; did you forget `prepare`?")
    actor_id = row["id"]

    clock = await get_clock(pool, run_id)
    when = at_virtual or (clock.virtual_now + timedelta(seconds=1))
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    if text is None:
        text = f"[live] hello from {row['handle']} @ {when.isoformat()}"
    if channel is None:
        channel = "general"

    event_id = uuid4()
    await pool.execute(
        """
        INSERT INTO timeline.events
            (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical)
        VALUES ($1, $2, $3, 'discord.message', $4, $5::jsonb, '{}'::jsonb, FALSE)
        """,
        event_id, run_id, when, actor_id,
        json.dumps({"channel": channel.lstrip("#"), "text": text, "kind": "live"}),
    )
    return event_id


async def inject_discord_interaction(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    handle: Optional[str] = None,
    command: str = "ping",
    interaction_type: int = 2,
    channel: Optional[str] = None,
    at_virtual: Optional[datetime] = None,
) -> UUID:
    """Append one ``discord.interaction`` event (not-historical).

    The Director emits this as an Ed25519-signed POST to the consumer's
    interactions endpoint. ``interaction_type``: 1=PING, 2=APPLICATION_COMMAND,
    3=MESSAGE_COMPONENT.
    """
    if handle is None:
        row = await pool.fetchrow(
            "SELECT id, handle FROM org.people WHERE run_id = $1 ORDER BY random() LIMIT 1",
            run_id,
        )
    else:
        row = await pool.fetchrow(
            "SELECT id, handle FROM org.people WHERE run_id = $1 AND handle = $2",
            run_id, handle,
        )
    if row is None:
        raise LookupError("no people in this run; did you forget `prepare`?")

    clock = await get_clock(pool, run_id)
    when = at_virtual or (clock.virtual_now + timedelta(seconds=1))
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    event_id = uuid4()
    await pool.execute(
        """
        INSERT INTO timeline.events
            (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical)
        VALUES ($1, $2, $3, 'discord.interaction', $4, $5::jsonb, '{}'::jsonb, FALSE)
        """,
        event_id, run_id, when, row["id"],
        json.dumps({
            "interaction_type": interaction_type,
            "command": command,
            "channel": (channel or "general").lstrip("#"),
            "kind": "live",
        }),
    )
    return event_id


# Content event kinds injectable as live GitHub webhooks, with GitHub's default
# webhook action for each (``push`` carries no action).
_GH_CONTENT_KINDS = (
    "pull_request", "issues", "push",
    "pull_request_review", "issue_comment", "check_run",
)
_GH_DEFAULT_ACTION = {
    "pull_request": "opened",
    "issues": "opened",
    "pull_request_review": "submitted",
    "issue_comment": "created",
    "check_run": "completed",
}
_GH_LIFECYCLE_KINDS = ("installation", "installation_repositories", "ping")


async def _gh_repo_row(pool, run_id: UUID, repo: Optional[str]):
    row = await pool.fetchrow(
        """
        SELECT r.id, r.owner, r.name, r.default_branch FROM app_github.repositories r
          JOIN app_github.installations inst ON inst.id = r.installation_pk
          JOIN app_github.apps a ON a.id = inst.app_pk
         WHERE a.run_id = $1 AND ($2::text IS NULL OR r.name = $2 OR (r.owner || '/' || r.name) = $2)
         ORDER BY r.name
         LIMIT 1
        """,
        run_id, repo,
    )
    if row is None:
        raise LookupError(f"no github repo for run {run_id} (repo={repo!r})")
    return row


async def _gh_person(pool, run_id: UUID, handle: Optional[str]):
    if handle is not None:
        person = await pool.fetchrow(
            "SELECT id, handle FROM org.people WHERE run_id = $1 AND handle = $2", run_id, handle
        )
    else:
        person = await pool.fetchrow(
            "SELECT id, handle FROM org.people WHERE run_id = $1 ORDER BY handle LIMIT 1", run_id
        )
    if person is None:
        raise LookupError(f"no people on run {run_id}; cannot attribute the event")
    return person["id"], person["handle"]


async def _gh_next_number(pool, repo_pk) -> int:
    # PRs and issues share one number sequence per repo, like real GitHub.
    return await pool.fetchval(
        """
        SELECT COALESCE(MAX(number), 0) + 1 FROM (
            SELECT number FROM app_github.pull_requests WHERE repo_pk = $1
            UNION ALL
            SELECT number FROM app_github.issues WHERE repo_pk = $1
        ) s
        """,
        repo_pk,
    )


async def inject_github_event(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    kind: str = "pull_request",
    action: Optional[str] = None,
    repo: Optional[str] = None,
    handle: Optional[str] = None,
    title: Optional[str] = None,
    body: Optional[str] = None,
    number: Optional[int] = None,
    review_state: str = "approved",
    check_conclusion: str = "success",
    at_virtual: Optional[datetime] = None,
) -> UUID:
    """Create/transition a live GitHub entity + a not-historical timeline event
    so the emission loop webhooks it, exactly like real GitHub.

    ``kind`` ∈ {pull_request, issues, push, pull_request_review, issue_comment,
    check_run}. ``action`` overrides GitHub's default webhook action — for PRs:
    opened/closed/reopened/synchronize/edited plus the convenience ``merged``
    (closes the PR with ``merged=true`` and a "closed" action, like GitHub); for
    issues: opened/closed/reopened/edited. ``number`` targets an existing PR/issue
    (required for reviews, comments and transitions); when omitted, opened events
    mint the next shared number. The entity is projected immediately so REST reads
    see it. Returns the new timeline event id.
    """
    if kind not in _GH_CONTENT_KINDS:
        raise ValueError(f"unsupported github inject kind: {kind!r}")

    repo_row = await _gh_repo_row(pool, run_id, repo)
    repo_pk, owner, name = repo_row["id"], repo_row["owner"], repo_row["name"]
    full = f"{owner}/{name}"
    actor_id, login = await _gh_person(pool, run_id, handle)

    clock = await get_clock(pool, run_id)
    vnow = at_virtual or clock.virtual_now
    if vnow.tzinfo is None:
        vnow = vnow.replace(tzinfo=timezone.utc)
    # Live objects must get strictly-increasing, DISTINCT timestamps even under a
    # frozen clock — real GitHub never collides created_at/updated_at, and a
    # consumer's `updated_at` cursor + reconciler baseline rely on monotonicity.
    # Bump one second per prior live github event, so each injection (including a
    # later merge/close) lands strictly after the object's own created_at, and no
    # two live objects share a timestamp.
    prior = await pool.fetchval(
        "SELECT count(*) FROM timeline.events "
        "WHERE run_id=$1 AND is_historical=FALSE AND type LIKE 'github.%'",
        run_id,
    )
    when = vnow + timedelta(seconds=int(prior) + 1)   # entity (created/updated/…) timestamp
    emit_ts = vnow                                      # timeline drains at <= virtual_now

    event_id = uuid4()
    etype = f"github.{kind}"

    async def _emit(payload: dict) -> UUID:
        await pool.execute(
            """
            INSERT INTO timeline.events
                (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, '{}'::jsonb, FALSE)
            """,
            event_id, run_id, emit_ts, etype, actor_id, json.dumps(payload),
        )
        return event_id

    # ----------------------------- pull_request -----------------------------
    if kind == "pull_request":
        act = action or _GH_DEFAULT_ACTION[kind]
        if number is None and act in ("opened", "reopened"):
            number = await _gh_next_number(pool, repo_pk)
            await _emit({"repo": full, "number": number, "action": act})
            await pool.execute(
                """
                INSERT INTO app_github.pull_requests
                    (id, repo_pk, number, title, body, state, merged, user_login,
                     head_ref, head_sha, base_sha, created_at, updated_at, timeline_event_id)
                VALUES ($1, $2, $3, $4, $5, 'open', FALSE, $6, $7, $8, $9, $10, $10, $11)
                """,
                uuid4(), repo_pk, number, title or f"Live PR #{number}", body or "",
                login, f"feature/live-{number}", github_sha(), github_sha(), when, event_id,
            )
            return event_id
        if number is None:
            raise ValueError(f"github pull_request action {act!r} needs a `number`")
        merged = act == "merged"
        webhook_action = "closed" if merged else act
        if webhook_action == "closed":
            await pool.execute(
                "UPDATE app_github.pull_requests SET state='closed', merged=$3, "
                "merged_at=CASE WHEN $3 THEN $4 ELSE merged_at END, closed_at=$4, updated_at=$4 "
                "WHERE repo_pk=$1 AND number=$2",
                repo_pk, number, merged, when,
            )
        elif webhook_action == "reopened":
            await pool.execute(
                "UPDATE app_github.pull_requests SET state='open', merged=FALSE, closed_at=NULL, "
                "updated_at=$3 WHERE repo_pk=$1 AND number=$2", repo_pk, number, when,
            )
        elif webhook_action == "synchronize":
            await pool.execute(
                "UPDATE app_github.pull_requests SET head_sha=$3, updated_at=$4 "
                "WHERE repo_pk=$1 AND number=$2", repo_pk, number, github_sha(), when,
            )
        else:  # edited / labeled / any other metadata change → bump updated_at
            await pool.execute(
                "UPDATE app_github.pull_requests SET updated_at=$3, title=COALESCE($4, title) "
                "WHERE repo_pk=$1 AND number=$2", repo_pk, number, when, title,
            )
        return await _emit({"repo": full, "number": number, "action": webhook_action})

    # -------------------------------- issues --------------------------------
    if kind == "issues":
        act = action or _GH_DEFAULT_ACTION[kind]
        if number is None and act in ("opened", "reopened"):
            number = await _gh_next_number(pool, repo_pk)
            await _emit({"repo": full, "number": number, "action": act})
            await pool.execute(
                """
                INSERT INTO app_github.issues
                    (id, repo_pk, number, title, body, state, user_login,
                     created_at, updated_at, timeline_event_id)
                VALUES ($1, $2, $3, $4, $5, 'open', $6, $7, $7, $8)
                """,
                uuid4(), repo_pk, number, title or f"Live issue #{number}", body or "",
                login, when, event_id,
            )
            return event_id
        if number is None:
            raise ValueError(f"github issues action {act!r} needs a `number`")
        if act == "closed":
            await pool.execute(
                "UPDATE app_github.issues SET state='closed', closed_at=$3, updated_at=$3 "
                "WHERE repo_pk=$1 AND number=$2", repo_pk, number, when,
            )
        elif act == "reopened":
            await pool.execute(
                "UPDATE app_github.issues SET state='open', closed_at=NULL, updated_at=$3 "
                "WHERE repo_pk=$1 AND number=$2", repo_pk, number, when,
            )
        else:  # edited / labeled
            await pool.execute(
                "UPDATE app_github.issues SET updated_at=$3, title=COALESCE($4, title) "
                "WHERE repo_pk=$1 AND number=$2", repo_pk, number, when, title,
            )
        return await _emit({"repo": full, "number": number, "action": act})

    # --------------------------------- push ---------------------------------
    if kind == "push":
        before = await pool.fetchval(
            "SELECT sha FROM app_github.commits WHERE repo_pk=$1 ORDER BY committed_at DESC LIMIT 1",
            repo_pk,
        )
        before = before or "0" * 40
        sha = github_sha()
        ref = f"refs/heads/{repo_row['default_branch']}"
        await _emit({"repo": full, "ref": ref, "before": before, "after": sha})
        await pool.execute(
            """
            INSERT INTO app_github.commits
                (id, repo_pk, sha, message, author_login, author_email, committed_at,
                 parents, additions, deletions)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10)
            """,
            uuid4(), repo_pk, sha, title or f"Live commit {sha[:7]}", login,
            f"{login}@users.noreply.github.com", when,
            json.dumps([before] if before != "0" * 40 else []), 1, 0,
        )
        return event_id

    # --------------------------- pull_request_review ------------------------
    if kind == "pull_request_review":
        act = action or _GH_DEFAULT_ACTION[kind]
        pr = await pool.fetchrow(
            "SELECT id, number FROM app_github.pull_requests WHERE repo_pk=$1 "
            "AND ($2::int IS NULL OR number=$2) ORDER BY (number=$2) DESC, number DESC LIMIT 1",
            repo_pk, number,
        )
        if pr is None:
            raise LookupError("no pull request to review; inject one first")
        await _emit({"repo": full, "number": pr["number"], "action": act})
        await pool.execute(
            "INSERT INTO app_github.reviews (id, pr_pk, user_login, state, body, submitted_at, timeline_event_id) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7)",
            uuid4(), pr["id"], login, review_state, body or "", when, event_id,
        )
        await pool.execute("UPDATE app_github.pull_requests SET updated_at=$2 WHERE id=$1", pr["id"], when)
        return event_id

    # ----------------------------- issue_comment ----------------------------
    if kind == "issue_comment":
        act = action or _GH_DEFAULT_ACTION[kind]
        issue_number = number
        if issue_number is None:
            issue_number = await pool.fetchval(
                "SELECT number FROM ("
                "  SELECT number FROM app_github.issues WHERE repo_pk=$1"
                "  UNION ALL SELECT number FROM app_github.pull_requests WHERE repo_pk=$1"
                ") s ORDER BY number DESC LIMIT 1",
                repo_pk,
            )
        if issue_number is None:
            raise LookupError("no issue/PR to comment on; inject one first")
        await _emit({"repo": full, "issue_number": issue_number, "action": act})
        await pool.execute(
            "INSERT INTO app_github.issue_comments (id, repo_pk, issue_number, user_login, body, created_at, timeline_event_id) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7)",
            uuid4(), repo_pk, issue_number, login, body or title or "Live comment.", when, event_id,
        )
        return event_id

    # ------------------------------- check_run ------------------------------
    if kind == "check_run":
        act = action or _GH_DEFAULT_ACTION[kind]
        if number is not None:
            head_sha = await pool.fetchval(
                "SELECT head_sha FROM app_github.pull_requests WHERE repo_pk=$1 AND number=$2",
                repo_pk, number,
            )
        else:
            head_sha = await pool.fetchval(
                "SELECT sha FROM app_github.commits WHERE repo_pk=$1 ORDER BY committed_at DESC LIMIT 1",
                repo_pk,
            )
        if head_sha is None:
            raise LookupError("no commit/PR head to attach a check run to; inject one first")
        completed = act == "completed"
        await _emit({"repo": full, "head_sha": head_sha, "action": act})
        await pool.execute(
            "INSERT INTO app_github.check_runs (id, repo_pk, name, head_sha, status, conclusion, started_at, completed_at, timeline_event_id) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
            uuid4(), repo_pk, title or "ci/build", head_sha,
            "completed" if completed else "in_progress",
            check_conclusion if completed else None, when, when if completed else None, event_id,
        )
        return event_id

    raise ValueError(f"unhandled github inject kind: {kind!r}")  # pragma: no cover


async def inject_github_lifecycle(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    kind: str,
    action: Optional[str] = None,
    repos: Optional[list[str]] = None,
    handle: Optional[str] = None,
    at_virtual: Optional[datetime] = None,
) -> UUID:
    """Inject an App-level GitHub webhook: ``installation`` (created/deleted/
    suspend/unsuspend), ``installation_repositories`` (added/removed), or ``ping``.

    These are NOT observations on the consumer side — they drive install
    enable/disable and the repo allowlist. ``suspend``/``deleted`` also flip the
    installation's ``suspended_at`` so the REST API starts returning the
    documented 404 for that token, exercising the revocation chokepoint too.
    Returns the new timeline event id.
    """
    if kind not in _GH_LIFECYCLE_KINDS:
        raise ValueError(f"unsupported github lifecycle kind: {kind!r}")
    actor_id, _login = await _gh_person(pool, run_id, handle)

    inst = await pool.fetchrow(
        """
        SELECT inst.id, inst.installation_id FROM app_github.installations inst
          JOIN app_github.apps a ON a.id = inst.app_pk
         WHERE a.run_id = $1 ORDER BY inst.installation_id LIMIT 1
        """,
        run_id,
    )
    if inst is None:
        raise LookupError(f"no github installation for run {run_id}")

    clock = await get_clock(pool, run_id)
    when = at_virtual or clock.virtual_now
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    if kind == "installation":
        act = action or "created"
        if act in ("suspend", "deleted"):
            await pool.execute(
                "UPDATE app_github.installations SET suspended_at=$2 WHERE id=$1", inst["id"], when
            )
        elif act in ("unsuspend", "created"):
            await pool.execute(
                "UPDATE app_github.installations SET suspended_at=NULL WHERE id=$1", inst["id"]
            )
        payload = {"action": act}
    elif kind == "installation_repositories":
        act = action or "removed"
        payload = {"action": act, "repos": repos or []}
    else:  # ping
        act = None
        payload = {}

    event_id = uuid4()
    await pool.execute(
        """
        INSERT INTO timeline.events
            (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, '{}'::jsonb, FALSE)
        """,
        event_id, run_id, when, f"github.{kind}", actor_id, json.dumps(payload),
    )
    return event_id


async def _live_person(pool, run_id: UUID, handle: Optional[str]):
    if handle is not None:
        row = await pool.fetchrow(
            "SELECT id, handle, full_name, email FROM org.people WHERE run_id=$1 AND handle=$2",
            run_id, handle)
    else:
        row = await pool.fetchrow(
            "SELECT id, handle, full_name, email FROM org.people WHERE run_id=$1 ORDER BY random() LIMIT 1",
            run_id)
    if row is None:
        raise LookupError("no people in this run; did you forget `prepare`?")
    return row


def _notion_rich_text(content: str) -> list:
    return [{"type": "text", "text": {"content": content, "link": None},
             "annotations": {"bold": False, "italic": False, "strikethrough": False,
                             "underline": False, "code": False, "color": "default"},
             "plain_text": content, "href": None}]


async def _notion_live_ts(pool: asyncpg.Pool, run_id: UUID, at_virtual: Optional[datetime]):
    """Return ``(virtual_ts, entity_ts)`` for a live Notion event.

    Like GitHub, live Notion objects must get strictly-increasing, DISTINCT
    ``created_time``/``last_edited_time`` even under a frozen clock — real Notion
    never collides them, and the consumer's reconciler high-water + cursor rely on
    monotonicity. The timeline event drains at ``virtual_ts = virtual_now`` while
    the entity timestamp is bumped one second per prior live notion event."""
    clock = await get_clock(pool, run_id)
    vnow = at_virtual or clock.virtual_now
    if vnow.tzinfo is None:
        vnow = vnow.replace(tzinfo=timezone.utc)
    prior = await pool.fetchval(
        "SELECT count(*) FROM timeline.events "
        "WHERE run_id=$1 AND is_historical=FALSE AND type='notion.page'",
        run_id)
    return vnow, vnow + timedelta(seconds=int(prior) + 1)


async def inject_notion_page(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    handle: Optional[str] = None,
    database: Optional[str] = None,
    title: Optional[str] = None,
    event_type: str = "page.created",
    at_virtual: Optional[datetime] = None,
) -> UUID:
    """Create a live Notion page in a database + a not-historical ``notion.page``
    event. The page is projected immediately (REST sees it); the event drives the
    signed thin webhook the consumer hydrates via GET /v1/pages/{id}."""
    integ = await pool.fetchrow(
        "SELECT id, bot_user_id FROM app_notion.integrations WHERE run_id=$1", run_id)
    if integ is None:
        raise LookupError("no notion integration in this run; did you forget `prepare`?")
    db = await pool.fetchrow(
        "SELECT id, database_id FROM app_notion.databases WHERE integration_pk=$1 "
        "AND ($2::text IS NULL OR title=$2) ORDER BY (title=$2) DESC, title LIMIT 1",
        integ["id"], database)
    if db is None:
        raise LookupError("no notion database in this run")
    person = await _live_person(pool, run_id, handle)
    vnow, entity_ts = await _notion_live_ts(pool, run_id, at_virtual)

    page_id = notion_id()
    ttl = (title or f"Live page @ {entity_ts.isoformat()}")[:120]
    event_id = uuid4()
    await pool.execute(
        """INSERT INTO timeline.events
            (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical)
           VALUES ($1,$2,$3,'notion.page',$4,$5::jsonb,'{}'::jsonb,FALSE)""",
        event_id, run_id, vnow, person["id"],
        json.dumps({"object": "page", "page_id": page_id, "event_type": event_type, "title": ttl}))

    props = {"Name": {"id": "title", "type": "title", "title": _notion_rich_text(ttl)},
             "Status": {"id": "stat%3A", "type": "select", "select": {"name": "Draft", "color": "default"}}}
    page_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_notion.pages
            (id, integration_pk, page_id, parent_type, parent_id, database_pk, title, properties,
             icon, archived, url, created_by, created_time, last_edited_time, timeline_event_id)
           VALUES ($1,$2,$3,'database_id',$4,$5,$6,$7::jsonb,NULL,FALSE,$8,$9,$10,$10,$11)""",
        page_pk, integ["id"], page_id, db["database_id"], db["id"], ttl, json.dumps(props),
        f"https://www.notion.so/{page_id.replace('-', '')}", integ["bot_user_id"], entity_ts, event_id)
    await pool.execute(
        """INSERT INTO app_notion.blocks
            (id, page_pk, block_id, parent_block_id, type, content, has_children, position,
             created_by, created_time, last_edited_time, timeline_event_id)
           VALUES ($1,$2,$3,NULL,'paragraph',$4::jsonb,FALSE,0,$5,$6,$6,$7)""",
        uuid4(), page_pk, notion_id(),
        json.dumps({"rich_text": _notion_rich_text("Injected live."), "color": "default"}),
        integ["bot_user_id"], entity_ts, event_id)
    return event_id


async def inject_notion_page_update(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    page_id: Optional[str] = None,
    event_type: str = "page.content_updated",
    at_virtual: Optional[datetime] = None,
) -> UUID:
    """Emit a live edit of an EXISTING page: bump its ``last_edited_time`` to a
    fresh distinct timestamp + emit a thin ``notion.page`` event. The consumer
    fetches the page back, and ``external_id = notion:page:{id}`` dedups it against
    the backfilled copy — so an update produces no NEW observation (the dedup
    invariant), but does advance the reconciler high-water."""
    integ = await pool.fetchrow("SELECT id FROM app_notion.integrations WHERE run_id=$1", run_id)
    if integ is None:
        raise LookupError("no notion integration in this run; did you forget `prepare`?")
    row = await pool.fetchrow(
        "SELECT id, page_id FROM app_notion.pages WHERE integration_pk=$1 "
        "AND ($2::text IS NULL OR page_id=$2) ORDER BY (page_id=$2) DESC, last_edited_time ASC LIMIT 1",
        integ["id"], page_id)
    if row is None:
        raise LookupError("no notion page to update in this run")
    person = await _live_person(pool, run_id, None)
    vnow, entity_ts = await _notion_live_ts(pool, run_id, at_virtual)
    event_id = uuid4()
    await pool.execute(
        """INSERT INTO timeline.events
            (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical)
           VALUES ($1,$2,$3,'notion.page',$4,$5::jsonb,'{}'::jsonb,FALSE)""",
        event_id, run_id, vnow, person["id"],
        json.dumps({"object": "page", "page_id": row["page_id"], "event_type": event_type}))
    await pool.execute(
        "UPDATE app_notion.pages SET last_edited_time=$2, timeline_event_id=$3 WHERE id=$1",
        row["id"], entity_ts, event_id)
    return event_id


async def inject_gmail_message(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    handle: Optional[str] = None,
    recipient: Optional[str] = None,
    text: Optional[str] = None,
    at_virtual: Optional[datetime] = None,
) -> UUID:
    """Send a live email (sender SENT + recipient INBOX, history bumped) + a
    not-historical ``gmail.message`` event carrying the recipient's mailbox +
    new historyId, which drives the OIDC-signed Pub/Sub push."""
    from email.utils import format_datetime, make_msgid
    cust = await pool.fetchrow("SELECT id, domain FROM app_gmail.customers WHERE run_id=$1", run_id)
    if cust is None:
        raise LookupError("no gmail customer in this run; did you forget `prepare`?")
    sender = await _live_person(pool, run_id, handle)
    if recipient is not None:
        recp = await pool.fetchrow(
            "SELECT id, handle, full_name, email FROM org.people WHERE run_id=$1 AND handle=$2",
            run_id, recipient)
    else:
        recp = None
    if recp is None or recp["id"] == sender["id"]:
        recp = await pool.fetchrow(
            "SELECT id, handle, full_name, email FROM org.people WHERE run_id=$1 AND id<>$2 LIMIT 1",
            run_id, sender["id"])
    if recp is None:
        raise LookupError("need at least two people to send mail")

    clock = await get_clock(pool, run_id)
    when = at_virtual or (clock.virtual_now + timedelta(seconds=1))
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    rfc_id = make_msgid(domain=cust["domain"])
    body_txt = text or f"[live] note from {sender['handle']} @ {when.isoformat()}"
    subject = body_txt.strip().splitlines()[0][:78]
    headers = [
        {"name": "From", "value": f"{sender['full_name']} <{sender['email']}>"},
        {"name": "To", "value": recp["email"]},
        {"name": "Subject", "value": subject},
        {"name": "Date", "value": format_datetime(when)},
        {"name": "Message-ID", "value": rfc_id},
    ]
    recipient_hid = 1
    for email_addr, labels, is_recipient in ((sender["email"], ["SENT"], False),
                                             (recp["email"], ["INBOX", "UNREAD"], True)):
        mbox = await pool.fetchrow(
            "SELECT id, history_id FROM app_gmail.mailboxes WHERE customer_pk=$1 AND email=$2",
            cust["id"], email_addr)
        if not mbox:
            continue
        new_hid = int(mbox["history_id"]) + 1
        tpk, gtid, gmid = uuid4(), gmail_thread_id(), gmail_message_id()
        await pool.execute(
            "INSERT INTO app_gmail.threads (id, mailbox_pk, thread_id, subject, snippet) "
            "VALUES ($1,$2,$3,$4,$5)", tpk, mbox["id"], gtid, subject, body_txt[:100])
        await pool.execute(
            """INSERT INTO app_gmail.messages
                (id, thread_pk, message_id, history_id, rfc822_msg_id, label_ids, headers,
                 snippet, body_plain, body_html, internal_date, size_estimate)
               VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8,$9,'',$10,$11)""",
            uuid4(), tpk, gmid, new_hid, rfc_id, json.dumps(labels), json.dumps(headers),
            body_txt[:120].replace("\n", " "), body_txt, when, len(body_txt) + 200)
        await pool.execute(
            """INSERT INTO app_gmail.history
                (mailbox_pk, history_id, history_type, message_id, thread_id, label_ids, occurred_at)
               VALUES ($1,$2,'messageAdded',$3,$4,$5::jsonb,$6)""",
            mbox["id"], new_hid, gmid, gtid, json.dumps(labels), when)
        await pool.execute("UPDATE app_gmail.mailboxes SET history_id=$1 WHERE id=$2", new_hid, mbox["id"])
        if is_recipient:
            recipient_hid = new_hid

    event_id = uuid4()
    await pool.execute(
        """INSERT INTO timeline.events
            (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical)
           VALUES ($1,$2,$3,'gmail.message',$4,$5::jsonb,'{}'::jsonb,FALSE)""",
        event_id, run_id, when, sender["id"],
        json.dumps({"email": recp["email"], "history_id": recipient_hid, "kind": "live"}))
    return event_id


async def inject_calendar_event(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    handle: Optional[str] = None,
    attendee: Optional[str] = None,
    text: Optional[str] = None,
    at_virtual: Optional[datetime] = None,
) -> UUID:
    """Create a live calendar event on the actor's calendar. Calendar is
    poll-only (no push), so this just projects the event with a fresh
    ``updated_at`` — the consumer's next incremental ``syncToken`` poll sees it.
    A ``calendar.event`` timeline row is recorded for bookkeeping."""
    organizer = await _live_person(pool, run_id, handle)
    cal = await pool.fetchrow(
        """SELECT c.id, c.calendar_id FROM app_calendar.calendars c
             JOIN app_calendar.accounts a ON a.id=c.account_pk
            WHERE a.run_id=$1 AND c.calendar_id=$2""", run_id, organizer["email"])
    if cal is None:
        raise LookupError("no calendar for this person; did you forget `prepare`?")
    clock = await get_clock(pool, run_id)
    when = at_virtual or (clock.virtual_now + timedelta(seconds=1))
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    end = when + timedelta(minutes=30)
    summary = (text or "Live meeting")[:200]
    attendees = [{"email": organizer["email"], "displayName": organizer["full_name"],
                  "organizer": True, "self": True, "responseStatus": "accepted"}]
    if attendee:
        att = await pool.fetchrow(
            "SELECT full_name, email FROM org.people WHERE run_id=$1 AND handle=$2", run_id, attendee)
        if att:
            attendees.append({"email": att["email"], "displayName": att["full_name"],
                              "responseStatus": "needsAction"})

    event_id = uuid4()
    await pool.execute(
        """INSERT INTO timeline.events
            (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical)
           VALUES ($1,$2,$3,'calendar.event',$4,$5::jsonb,'{}'::jsonb,FALSE)""",
        event_id, run_id, when, organizer["id"],
        json.dumps({"summary": summary, "kind": "live"}))
    gid = gcal_event_id()
    await pool.execute(
        """INSERT INTO app_calendar.events
            (id, calendar_pk, event_id, status, summary, description, location,
             start_time, end_time, all_day, organizer_email, creator_email, attendees,
             recurring_event_id, event_type, hangout_link, html_link, sequence, ical_uid,
             created_at, updated_at, timeline_event_id)
           VALUES ($1,$2,$3,'confirmed',$4,'','',$5,$6,FALSE,$7,$7,$8::jsonb,
                   NULL,'default',NULL,$9,0,$10,$11,$11,$12)""",
        uuid4(), cal["id"], gid, summary, when, end, organizer["email"], json.dumps(attendees),
        f"https://www.google.com/calendar/event?eid={gid}", gcal_ical_uid(), when, event_id)
    return event_id


async def inject_drive_file(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    handle: Optional[str] = None,
    title: Optional[str] = None,
    trash: bool = False,
    hard: bool = False,
    at_virtual: Optional[datetime] = None,
) -> UUID:
    """Land a live Drive change on the actor's My Drive. Drive is poll-only (no
    push), so this just bumps ``change_seq`` (= max+1) past the consumer's
    warm-start token; the next ``changes.list`` surfaces it.

      - default: create a new file (a ``signal`` observation).
      - ``trash=True``: trash an existing active file → the changes feed reports
        ``file.trashed=true`` (or ``removed=true`` when ``hard``) → a
        ``state_change`` observation. Faithful: a removal is only observable
        incrementally, never in the trashed=false backfill.
    """
    inst = await pool.fetchrow("SELECT id FROM app_drive.installations WHERE run_id=$1", run_id)
    if inst is None:
        raise LookupError("no drive installation in this run; did you forget `prepare`?")
    owner = await _live_person(pool, run_id, handle)
    drive = await pool.fetchrow(
        "SELECT id FROM app_drive.drives WHERE installation_pk=$1 AND kind='my_drive' AND owner_email=$2",
        inst["id"], owner["email"])
    if drive is None:
        raise LookupError("no My Drive for this person; did you forget `prepare`?")
    clock = await get_clock(pool, run_id)
    when = at_virtual or (clock.virtual_now + timedelta(seconds=1))
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    seq = int(await pool.fetchval(
        "SELECT COALESCE(MAX(change_seq), 0) FROM app_drive.files WHERE installation_pk=$1",
        inst["id"])) + 1

    if trash:
        target = await pool.fetchrow(
            "SELECT id, file_id, name FROM app_drive.files "
            "WHERE drive_pk=$1 AND trashed=FALSE ORDER BY change_seq DESC LIMIT 1", drive["id"])
        if target is None:
            raise LookupError("no active file on this My Drive to trash")
        event_id = uuid4()
        await pool.execute(
            """INSERT INTO timeline.events
                (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical)
               VALUES ($1,$2,$3,'drive.file',$4,$5::jsonb,'{}'::jsonb,FALSE)""",
            event_id, run_id, when, owner["id"],
            json.dumps({"file_id": target["file_id"], "name": target["name"],
                        "kind": "trash", "removed": bool(hard)}))
        await pool.execute(
            "UPDATE app_drive.files SET trashed=TRUE, explicitly_trashed=$2, "
            "modified_time=$3, change_seq=$4, timeline_event_id=$5 WHERE id=$1",
            target["id"], bool(hard), when, seq, event_id)
        return event_id

    name = (title or f"Live doc @ {when.isoformat()}")[:120]
    text = f"Injected live document by {owner['handle']}."
    fid = drive_file_id()
    event_id = uuid4()
    await pool.execute(
        """INSERT INTO timeline.events
            (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical)
           VALUES ($1,$2,$3,'drive.file',$4,$5::jsonb,'{}'::jsonb,FALSE)""",
        event_id, run_id, when, owner["id"],
        json.dumps({"file_id": fid, "name": name, "kind": "live"}))
    await pool.execute(
        """INSERT INTO app_drive.files
            (id, installation_pk, drive_pk, file_id, name, mime_type, version, trashed,
             explicitly_trashed, size, web_view_link, owner_email, owner_name,
             last_modifying_email, last_modifying_name, parents, shared, starred,
             extracted_text, created_time, modified_time, change_seq, timeline_event_id)
           VALUES ($1,$2,$3,$4,$5,'application/vnd.google-apps.document',1,FALSE,FALSE,NULL,
                   $6,$7,$8,$7,$8,'[]'::jsonb,FALSE,FALSE,$9,$10,$10,$11,$12)""",
        uuid4(), inst["id"], drive["id"], fid, name,
        f"https://drive.google.com/file/d/{fid}/view", owner["email"], owner["full_name"],
        text, when, seq, event_id)
    return event_id


async def inject_jira_issue(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    handle: Optional[str] = None,
    project: Optional[str] = None,
    summary: Optional[str] = None,
    at_virtual: Optional[datetime] = None,
) -> UUID:
    """Create a live Jira issue + a status-transition changelog on the actor's
    account, plus a not-historical ``jira.issue`` event that drives the signed
    (``X-Hub-Signature``) webhook. The issue/changelog also land in the served
    tables, so the poll-incremental path (``updated >=``) sees it too."""
    inst = await pool.fetchrow("SELECT id FROM app_jira.installations WHERE run_id=$1", run_id)
    if inst is None:
        raise LookupError("no jira installation in this run; did you forget `prepare`?")
    proj = await pool.fetchrow(
        "SELECT id, key FROM app_jira.projects WHERE installation_pk=$1 "
        "AND ($2::text IS NULL OR key=$2) ORDER BY (key=$2) DESC, key LIMIT 1",
        inst["id"], project)
    if proj is None:
        raise LookupError("no jira project in this run")
    reporter = await _live_person(pool, run_id, handle)
    ru = await pool.fetchrow(
        "SELECT account_id FROM app_jira.users WHERE installation_pk=$1 AND person_id=$2",
        inst["id"], reporter["id"])
    acct = ru["account_id"] if ru else None
    clock = await get_clock(pool, run_id)
    vnow = clock.virtual_now
    if vnow.tzinfo is None:
        vnow = vnow.replace(tzinfo=timezone.utc)
    # Timeline event drains when virtual_ts <= virtual_now (so default = vnow, NOT
    # vnow+1s which would never drain under a frozen clock). The ISSUE `updated`
    # must be strictly-increasing + DISTINCT and PAST the backfill high-water so
    # the reconciler's `updated >= floor` probe and the JQL cursor see it as new;
    # real Jira never collides `updated` across edits.
    prior_live = await pool.fetchval(
        "SELECT count(*) FROM timeline.events "
        "WHERE run_id=$1 AND type='jira.issue' AND is_historical=FALSE", run_id)
    when_timeline = at_virtual or vnow
    if when_timeline.tzinfo is None:
        when_timeline = when_timeline.replace(tzinfo=timezone.utc)
    when_entity = at_virtual or (vnow + timedelta(seconds=int(prior_live) + 1))
    if when_entity.tzinfo is None:
        when_entity = when_entity.replace(tzinfo=timezone.utc)

    import secrets as _secrets
    n = int(await pool.fetchval(
        "SELECT COALESCE(MAX((regexp_replace(issue_key,'^.*-','' ))::int), 0) "
        "FROM app_jira.issues WHERE project_pk=$1", proj["id"])) + 1
    issue_key = f"{proj['key']}-{n}"
    issue_id = str(_secrets.randbelow(900000) + 100000)
    history_id = str(_secrets.randbelow(900000) + 100000)
    summ = (summary or f"Live issue from {reporter['handle']}")[:200]
    desc = {"type": "doc", "version": 1, "content": [{"type": "paragraph",
            "content": [{"type": "text", "text": "Injected live."}]}]}
    issue_pk = uuid4()
    event_id = uuid4()
    await pool.execute(
        """INSERT INTO timeline.events
            (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical)
           VALUES ($1,$2,$3,'jira.issue',$4,$5::jsonb,'{}'::jsonb,FALSE)""",
        event_id, run_id, when_timeline, reporter["id"],
        json.dumps({"issue_id": issue_id, "issue_key": issue_key, "history_id": history_id,
                    "kind": "issue_updated"}))
    await pool.execute(
        """INSERT INTO app_jira.issues
            (id, installation_pk, project_pk, issue_id, issue_key, summary, description,
             issue_type, status, status_category, priority, resolution, resolution_date,
             assignee_account_id, reporter_account_id, creator_account_id, labels, components,
             story_points, created_at, updated_at, timeline_event_id)
           VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,'Task','In Progress','indeterminate','Medium',
                   NULL,NULL,$8,$8,$8,'["live"]'::jsonb,'[]'::jsonb,NULL,$9,$9,$10)""",
        issue_pk, inst["id"], proj["id"], issue_id, issue_key, summ, json.dumps(desc),
        acct, when_entity, event_id)
    items = [{"field": "status", "fieldtype": "jira", "fieldId": "status",
              "from": "1", "fromString": "To Do", "to": "2", "toString": "In Progress"}]
    await pool.execute(
        """INSERT INTO app_jira.changelogs (id, issue_pk, history_id, author_account_id, items, created_at, position)
           VALUES ($1,$2,$3,$4,$5::jsonb,$6,0)""",
        uuid4(), issue_pk, history_id, acct, json.dumps(items), when_entity)
    return event_id


async def inject_quickbooks_change(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    entity_name: str = "Bill",
    operation: str = "Create",
    amount_usd: Optional[int] = None,
    memo: Optional[str] = None,
    at_virtual: Optional[datetime] = None,
) -> UUID:
    """Append a new QBO transaction (a ``purchase`` -> a new Bill/BillPayment) plus
    a thin ``quickbooks.change`` timeline event that drives the signed Intuit
    ``eventNotifications`` webhook. The notification is body-less, so the consumer
    re-queries the entity by ``Id`` to fetch it (the new purchase row makes the
    Bill/BillPayment queryable)."""
    import secrets as _secrets
    company = await pool.fetchrow(
        "SELECT id, realm_id FROM app_quickbooks.companies WHERE run_id=$1", run_id)
    if company is None:
        raise LookupError("no quickbooks company in this run; did you forget `prepare`?")
    company_pk = company["id"]
    vendor = await pool.fetchrow(
        "SELECT id, vendor_id, display_name FROM app_quickbooks.vendors "
        "WHERE company_pk=$1 ORDER BY random() LIMIT 1", company_pk)
    expense = await pool.fetchval(
        "SELECT id FROM app_quickbooks.accounts WHERE company_pk=$1 AND account_number=$2",
        company_pk, "5000")
    payacct = await pool.fetchval(
        "SELECT id FROM app_quickbooks.accounts WHERE company_pk=$1 AND account_number=$2",
        company_pk, "1000")
    actor = await pool.fetchval(
        "SELECT id FROM org.people WHERE run_id=$1 ORDER BY random() LIMIT 1", run_id)
    clock = await get_clock(pool, run_id)
    when = at_virtual or clock.virtual_now
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    purchase_id = f"live-{_secrets.token_hex(6)}"
    # The notification id is the ENTITY's Id: Bill==purchase_id, BillPayment=='BP-'+id.
    entity_id = f"BP-{purchase_id}" if entity_name == "BillPayment" else purchase_id
    amount_cents = int(amount_usd if amount_usd is not None
                       else _secrets.randbelow(9000) + 1000) * 100
    event_id = uuid4()
    await pool.execute(
        """INSERT INTO timeline.events
            (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical)
           VALUES ($1,$2,$3,'quickbooks.change',$4,$5::jsonb,'{}'::jsonb,FALSE)""",
        event_id, run_id, when, actor,
        json.dumps({"entity_name": entity_name, "entity_id": entity_id,
                    "operation": operation, "realm_id": company["realm_id"]}))
    await pool.execute(
        """INSERT INTO app_quickbooks.purchases
            (id, company_pk, purchase_id, txn_date, amount_cents, vendor_pk,
             expense_account_pk, payment_account_pk, category, memo, payload, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'live',$9,'{}'::jsonb,$10)""",
        uuid4(), company_pk, purchase_id, when.date(), amount_cents,
        vendor["id"] if vendor else None, expense, payacct,
        memo or "Live QBO change", when)
    return event_id


async def inject_grafana_alert(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    alertname: str = "HighErrorRate",
    status: str = "firing",
    severity: str = "critical",
    service: str = "api-gateway",
    summary: Optional[str] = None,
    at_virtual: Optional[datetime] = None,
) -> UUID:
    """Append a thin ``grafana.alert`` timeline event that drives the signed
    Grafana **Alerting webhook** (an Alertmanager-superset alert group).

    Grafana's live push channel is the alert webhook (``grafana:alert``), distinct
    from the pulled annotations channel. The event payload carries the alert's
    labels/annotations/timestamps; ``grafana/webhooks.py`` expands it into the full
    alert-group body and signs it ``X-Grafana-Alerting-Signature`` (bare hex)."""
    import hashlib
    import secrets as _secrets
    inst = await pool.fetchrow(
        "SELECT id, instance_host FROM app_grafana.instances WHERE run_id=$1", run_id)
    if inst is None:
        raise LookupError("no grafana instance in this run; did you forget `prepare`?")
    actor = await pool.fetchval(
        "SELECT id FROM org.people WHERE run_id=$1 ORDER BY random() LIMIT 1", run_id)
    clock = await get_clock(pool, run_id)
    when = at_virtual or clock.virtual_now
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    labels = {"alertname": alertname, "severity": severity,
              "service": service, "job": service}
    fingerprint = hashlib.blake2b(
        json.dumps(labels, sort_keys=True).encode(), digest_size=8).hexdigest()
    group_key = "{}/{alertname=\"%s\"}:{}" % alertname
    starts = when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    payload = {
        "status": status,
        "alertname": alertname,
        "labels": labels,
        "annotations": {
            "summary": summary or f"{alertname} on {service}",
            "description": f"{alertname}: {service} crossed its alert threshold",
            "runbook_url": f"https://{inst['instance_host']}/runbooks/{alertname.lower()}",
        },
        "starts_at": starts,
        "ends_at": None if status == "firing" else starts,
        "group_key": group_key,
        "fingerprint": fingerprint,
        "generator_url": f"https://{inst['instance_host']}/alerting/grafana/{_secrets.token_hex(6)}/view",
    }
    event_id = uuid4()
    await pool.execute(
        """INSERT INTO timeline.events
            (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical)
           VALUES ($1,$2,$3,'grafana.alert',$4,$5::jsonb,'{}'::jsonb,FALSE)""",
        event_id, run_id, when, actor, json.dumps(payload))
    return event_id


async def inject_mercury_transaction(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    amount_usd: Optional[float] = None,
    counterparty: str = "Stripe Inc.",
    kind: str = "externalTransfer",
    operation: str = "create",
    at_virtual: Optional[datetime] = None,
) -> UUID:
    """Append a thin ``mercury.transaction`` timeline event that drives the signed
    Mercury **transaction webhook** (a JSON-merge-patch event).

    Mercury's live push channel is the transaction webhook (``Mercury-Signature:
    t=…,v1=…``), distinct from the pulled accounts/transactions channel. A ``create``
    inserts a fresh **pending** checking transaction and carries the full resource
    as the merge patch, so the consumer can re-fetch
    ``GET /account/{accountId}/transaction/{id}`` (the row is persisted here)."""
    import secrets as _secrets
    org = await pool.fetchrow(
        "SELECT id FROM app_mercury.organizations WHERE run_id=$1", run_id)
    if org is None:
        raise LookupError("no mercury organization in this run; did you forget `prepare`?")
    acct = await pool.fetchrow(
        "SELECT id, account_id FROM app_mercury.accounts "
        "WHERE org_pk=$1 AND kind='checking' ORDER BY sort_key LIMIT 1", org["id"])
    if acct is None:
        raise LookupError("no checking account for this mercury org")

    clock = await get_clock(pool, run_id)
    when = at_virtual or clock.virtual_now
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    # A live vendor payment is an outflow: negative amount, pending until it settles.
    cents = int(round((amount_usd if amount_usd is not None
                       else _secrets.randbelow(90_000) + 1_000) * 100))
    amount_cents = -abs(cents)
    txn_id = uuid4()
    cp_id = uuid4()
    edd = when + timedelta(days=1)
    amount_usd_val = round(amount_cents / 100, 2)
    dashboard = f"https://mercury.com/transactions/{txn_id}"
    await pool.execute(
        """INSERT INTO app_mercury.transactions
            (id, account_pk, txn_id, amount_cents, status, kind, counterparty_id,
             counterparty_name, counterparty_nickname, external_memo, dashboard_link,
             version, created_at, posted_at, estimated_delivery_date, is_historical)
           VALUES ($1,$2,$1,$3,'pending',$4,$5,$6,$6,$7,$8,1,$9,NULL,$10,FALSE)""",
        txn_id, acct["id"], amount_cents, kind, cp_id, counterparty,
        f"Payment to {counterparty}", dashboard, when, edd)

    merge_patch = {
        "id": str(txn_id),
        "accountId": str(acct["account_id"]),
        "amount": amount_usd_val,
        "status": "pending",
        "kind": kind,
        "counterpartyId": str(cp_id),
        "counterpartyName": counterparty,
        "createdAt": when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "postedAt": None,
        "estimatedDeliveryDate": edd.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dashboardLink": dashboard,
    }
    payload = {
        "account_id": str(acct["account_id"]),
        "txn_id": str(txn_id),
        "operation": operation,
        "resource_version": 1,
        "changed_paths": sorted(merge_patch.keys()),
        "merge_patch": merge_patch,
        "previous_values": {},
    }
    actor = await pool.fetchval(
        "SELECT id FROM org.people WHERE run_id=$1 ORDER BY random() LIMIT 1", run_id)
    event_id = uuid4()
    await pool.execute(
        """INSERT INTO timeline.events
            (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical)
           VALUES ($1,$2,$3,'mercury.transaction',$4,$5::jsonb,'{}'::jsonb,FALSE)""",
        event_id, run_id, when, actor, json.dumps(payload))
    return event_id


async def inject_ashby_application_change(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    action: str = "applicationSubmit",
    candidate_name: Optional[str] = None,
    at_virtual: Optional[datetime] = None,
) -> UUID:
    """Append a thin ``ashby.object`` timeline event that drives the signed Ashby
    **webhook** (``{action, data:{application:{…}}}``, ``Ashby-Signature: sha256=…``).

    Ashby's live push channel is the HMAC-signed webhook, distinct from the pulled
    ``.list`` channel. A new application is inserted into ``app_ashby.entities`` (so
    a consumer can re-fetch it via ``application.info``) and the FULL application
    body rides in the webhook ``data`` — Ashby webhooks carry the whole entity, so
    the consumer can build its draft directly. The org dedups on
    ``ashby:{org}:application:{id}`` (NOT version-suffixed; relies on ``updatedAt``).
    """
    from spammers.ashby import dto as ashby_dto

    org = await pool.fetchrow(
        "SELECT id FROM app_ashby.organizations WHERE run_id=$1", run_id)
    if org is None:
        raise LookupError("no ashby organization in this run; did you forget `prepare`?")
    cand = await pool.fetchrow(
        "SELECT entity_id, data FROM app_ashby.entities "
        "WHERE org_pk=$1 AND kind='candidate' ORDER BY random() LIMIT 1", org["id"])
    if cand is None:
        raise LookupError("no ashby candidate in this run to attach an application to")
    job = await pool.fetchrow(
        "SELECT data FROM app_ashby.entities "
        "WHERE org_pk=$1 AND kind='job' AND status='Open' ORDER BY random() LIMIT 1",
        org["id"])
    if job is None:
        job = await pool.fetchrow(
            "SELECT data FROM app_ashby.entities WHERE org_pk=$1 AND kind='job' "
            "ORDER BY random() LIMIT 1", org["id"])
    if job is None:
        raise LookupError("no ashby job in this run to apply to")

    cdata = cand["data"] if isinstance(cand["data"], dict) else json.loads(cand["data"])
    jdata = job["data"] if isinstance(job["data"], dict) else json.loads(job["data"])

    clock = await get_clock(pool, run_id)
    when = at_virtual or clock.virtual_now
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    app_id = uuid4()
    cand_ref = {"id": str(cand["entity_id"]), "name": cdata.get("name"),
                "primaryEmailAddress": cdata.get("primaryEmailAddress"),
                "primaryPhoneNumber": cdata.get("primaryPhoneNumber")}
    job_ref = {"id": jdata.get("id"), "title": jdata.get("title"),
               "locationId": jdata.get("locationId"), "departmentId": jdata.get("departmentId")}
    stage = {"id": str(uuid4()), "title": "Application Review",
             "type": "PreInterviewScreen", "orderInInterviewPlan": 0,
             "interviewStageGroupId": str(uuid4()),
             "interviewPlanId": jdata.get("defaultInterviewPlanId")}
    source = {"id": str(uuid4()), "title": "Company Website",
              "isArchived": False, "sourceType": "JobPost"}
    app_data = ashby_dto.application_dto(
        entity_id=str(app_id), created_at=when, updated_at=when, status="Active",
        candidate_ref=cand_ref, current_stage=stage, job_ref=job_ref, source=source,
        hiring_team=[])

    await pool.execute(
        """INSERT INTO app_ashby.entities
            (id, org_pk, kind, entity_id, status, data, created_at, updated_at,
             is_historical, timeline_event_id)
           VALUES ($1,$2,'application',$1,'Active',$3::jsonb,$4,$4,FALSE,$5)""",
        app_id, org["id"], json.dumps(app_data), when, app_id)

    payload = {"action": action, "data": {"application": app_data}}
    actor = await pool.fetchval(
        "SELECT id FROM org.people WHERE run_id=$1 ORDER BY random() LIMIT 1", run_id)
    event_id = uuid4()
    await pool.execute(
        """INSERT INTO timeline.events
            (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical)
           VALUES ($1,$2,$3,'ashby.object',$4,$5::jsonb,'{}'::jsonb,FALSE)""",
        event_id, run_id, when, actor, json.dumps(payload))
    return event_id


async def inject_brex_transfer(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    amount_usd: Optional[float] = None,
    counterparty: str = "Stripe Inc.",
    payment_type: str = "ACH",
    event_type: str = "TRANSFER_PROCESSED",
    at_virtual: Optional[datetime] = None,
) -> UUID:
    """Append a thin ``brex.transfer`` timeline event that drives the signed Brex
    **webhook** (Svix scheme — ``Webhook-Signature: v1,<base64>``).

    Brex's live push channel is the ``TRANSFER_PROCESSED`` / ``TRANSFER_FAILED``
    webhook (transfer_id + company_id only — the full transfer detail lives behind
    the Payments API, out of scope). To make the event correlatable the inject also
    writes a fresh **cash transaction** carrying the same ``transfer_id`` onto the
    primary cash account, so a consumer can fetch-on-notify by listing
    ``GET /v2/transactions/cash/{id}`` and matching ``transfer_id`` (the wire field
    cash transactions expose)."""
    import secrets as _secrets
    org = await pool.fetchrow(
        "SELECT id FROM app_brex.organizations WHERE run_id=$1", run_id)
    if org is None:
        raise LookupError("no brex organization in this run; did you forget `prepare`?")
    acct = await pool.fetchrow(
        "SELECT id, account_id FROM app_brex.accounts "
        "WHERE org_pk=$1 AND kind='cash' AND is_primary ORDER BY sort_key LIMIT 1", org["id"])
    if acct is None:
        raise LookupError("no primary cash account for this brex org")

    clock = await get_clock(pool, run_id)
    when = at_virtual or clock.virtual_now
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    # A processed transfer is incoming cash: a positive PAYMENT on the cash account.
    cents = int(round((amount_usd if amount_usd is not None
                       else _secrets.randbelow(90_000) + 1_000) * 100))
    amount_cents = abs(cents)
    transfer_id = "trnsfr_" + _secrets.token_hex(12)
    txn_id = "txn_" + _secrets.token_hex(12)
    await pool.execute(
        """INSERT INTO app_brex.transactions
            (id, account_pk, account_kind, txn_id, description, amount_cents, currency,
             txn_type, initiated_at, posted_at, transfer_id, sort_key, is_historical)
           VALUES ($1,$2,'cash',$3,$4,$5,'USD','PAYMENT',$6,$6,$7,$8,FALSE)""",
        uuid4(), acct["id"], txn_id, f"Transfer from {counterparty}", amount_cents,
        when, transfer_id, int(when.timestamp()))

    payload = {
        "transfer_id": transfer_id,
        "payment_type": payment_type,
        "event_type": event_type,
        "account_id": str(acct["account_id"]),
    }
    actor = await pool.fetchval(
        "SELECT id FROM org.people WHERE run_id=$1 ORDER BY random() LIMIT 1", run_id)
    event_id = uuid4()
    await pool.execute(
        """INSERT INTO timeline.events
            (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical)
           VALUES ($1,$2,$3,'brex.transfer',$4,$5::jsonb,'{}'::jsonb,FALSE)""",
        event_id, run_id, when, actor, json.dumps(payload))
    return event_id


async def inject_ramp_transaction(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    amount_usd: Optional[float] = None,
    merchant: str = "Amazon Web Services",
    event_type: str = "transactions.cleared",
    at_virtual: Optional[datetime] = None,
) -> UUID:
    """Append a thin ``ramp.transaction`` timeline event that drives the signed Ramp
    **webhook** (``X-Ramp-Signature`` bare-hex HMAC over the raw body).

    Ramp's live push channel is the thin transaction event ``{id, type, created_at,
    business_id, object:{id}}`` — it carries only the resource id, so the inject also
    writes a fresh **CLEARED transaction** for that id onto the org, letting a consumer
    fetch-on-notify ``GET /developer/v1/transactions/{id}`` to pull the full record."""
    import secrets as _secrets
    org = await pool.fetchrow(
        "SELECT id, business_id FROM app_ramp.organizations WHERE run_id=$1", run_id)
    if org is None:
        raise LookupError("no ramp organization in this run; did you forget `prepare`?")
    holder = await pool.fetchrow(
        "SELECT c.card_id, c.cardholder_id, c.cardholder_name FROM app_ramp.cards c "
        "WHERE c.org_pk=$1 ORDER BY c.sort_key LIMIT 1", org["id"])
    next_sort = await pool.fetchval(
        "SELECT COALESCE(MAX(sort_key), 0) + 1 FROM app_ramp.transactions WHERE org_pk=$1",
        org["id"])

    clock = await get_clock(pool, run_id)
    when = at_virtual or clock.virtual_now
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    cents = int(round((amount_usd if amount_usd is not None
                       else _secrets.randbelow(90_000) + 1_000) * 100))
    txn_id = str(UUID(int=_secrets.randbits(128), version=4))
    settled = when + timedelta(days=1)
    await pool.execute(
        """INSERT INTO app_ramp.transactions
            (id, org_pk, txn_id, amount_cents, currency_code, state, sync_status,
             card_id, card_present, user_id, cardholder_name, merchant_id, merchant_name,
             merchant_category_code, user_transaction_time, accounting_date,
             settlement_date, synced_at, sort_key, is_historical)
           VALUES ($1,$2,$3,$4,'USD','CLEARED','SYNCED',$5,FALSE,$6,$7,$8,$9,'5734',
                   $10,$11,$11,NULL,$12,FALSE)""",
        uuid4(), org["id"], txn_id, abs(cents),
        holder["card_id"] if holder else None,
        holder["cardholder_id"] if holder else None,
        holder["cardholder_name"] if holder else None,
        str(UUID(int=_secrets.randbits(128), version=4)), merchant, when, settled,
        next_sort)

    payload = {"txn_id": txn_id, "event_type": event_type,
               "business_id": org["business_id"]}
    actor = await pool.fetchval(
        "SELECT id FROM org.people WHERE run_id=$1 ORDER BY random() LIMIT 1", run_id)
    event_id = uuid4()
    await pool.execute(
        """INSERT INTO timeline.events
            (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical)
           VALUES ($1,$2,$3,'ramp.transaction',$4,$5::jsonb,'{}'::jsonb,FALSE)""",
        event_id, run_id, when, actor, json.dumps(payload))
    return event_id


async def inject_gusto_event(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    target: str = "payroll",
    event_type: Optional[str] = None,
    at_virtual: Optional[datetime] = None,
) -> UUID:
    """Append a thin ``gusto.event`` timeline event that drives the signed Gusto
    **webhook** (``X-Gusto-Signature`` bare-hex HMAC over the raw body).

    Gusto's live push channel is the thin event ``{uuid, event_type, resource_type,
    resource_uuid, entity_type, entity_uuid, timestamp}`` — it carries only
    references, so the inject also materialises the referenced record (a fresh
    processed PAYROLL, or an employee version bump) so a consumer can fetch-on-notify
    ``GET /v1/companies/{co}/payrolls/{uuid}`` to pull the full record."""
    import secrets as _secrets
    co = await pool.fetchrow(
        "SELECT id, company_uuid, pay_schedule_uuid FROM app_gusto.companies WHERE run_id=$1",
        run_id)
    if co is None:
        raise LookupError("no gusto company in this run; did you forget `prepare`?")

    clock = await get_clock(pool, run_id)
    when = at_virtual or clock.virtual_now
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    if target == "employee":
        emp = await pool.fetchrow(
            "SELECT employee_uuid FROM app_gusto.employees WHERE company_pk=$1 "
            "AND terminated=FALSE ORDER BY sort_key LIMIT 1", co["id"])
        if emp is None:
            raise LookupError("no active gusto employee to update")
        resource_uuid = emp["employee_uuid"]
        new_version = "%032x" % _secrets.randbits(128)
        await pool.execute(
            "UPDATE app_gusto.employees SET version=$3, is_historical=FALSE "
            "WHERE company_pk=$1 AND employee_uuid=$2",
            co["id"], resource_uuid, new_version)
        payload = {"resource_type": "Employee", "resource_uuid": resource_uuid,
                   "event_type": event_type or "employee.updated"}
    else:
        # materialise a fresh processed payroll for this period
        next_sort = await pool.fetchval(
            "SELECT COALESCE(MAX(sort_key), 0) + 1 FROM app_gusto.payrolls WHERE company_pk=$1",
            co["id"])
        period_gross = await pool.fetchval(
            "SELECT COALESCE(SUM(rate_cents) / 24, 0) FROM app_gusto.employees "
            "WHERE company_pk=$1 AND terminated=FALSE", co["id"]) or 1_000_00
        gross = int(period_gross)
        emp_taxes = int(gross * 0.18)
        net = gross - emp_taxes - int(gross * 0.04)
        resource_uuid = str(UUID(int=_secrets.randbits(128), version=4))
        check = when.date()
        await pool.execute(
            """INSERT INTO app_gusto.payrolls
                (id, company_pk, payroll_uuid, pay_period_start, pay_period_end,
                 check_date, pay_schedule_uuid, processed, off_cycle, external,
                 payroll_type, processed_at, calculated_at, payroll_deadline,
                 gross_pay_cents, net_pay_cents, employer_taxes_cents,
                 employee_taxes_cents, benefits_cents, reimbursements_cents,
                 sort_key, is_historical)
               VALUES ($1,$2,$3,$4,$5,$6,$7,TRUE,FALSE,FALSE,'regular',$8,$8,$8,
                       $9,$10,$11,$12,$13,0,$14,FALSE)""",
            uuid4(), co["id"], resource_uuid, check - timedelta(days=16),
            check - timedelta(days=3), check, co["pay_schedule_uuid"], when,
            gross, net, int(gross * 0.0765), emp_taxes, int(gross * 0.04), next_sort)
        payload = {"resource_type": "Payroll", "resource_uuid": resource_uuid,
                   "event_type": event_type or "payroll.processed"}

    actor = await pool.fetchval(
        "SELECT id FROM org.people WHERE run_id=$1 ORDER BY random() LIMIT 1", run_id)
    event_id = uuid4()
    await pool.execute(
        """INSERT INTO timeline.events
            (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical)
           VALUES ($1,$2,$3,'gusto.event',$4,$5::jsonb,'{}'::jsonb,FALSE)""",
        event_id, run_id, when, actor, json.dumps(payload))
    return event_id


async def inject_deel_event(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    event_type: str = "invoice.paid",
    amount_usd: Optional[float] = None,
    at_virtual: Optional[datetime] = None,
) -> UUID:
    """Append a thin ``deel.event`` timeline event that drives the signed Deel
    **webhook** (``x-deel-signature`` bare-hex HMAC over ``"POST"+body``).

    Deel's live push channel is the nested ``{data:{meta:{event_type,
    organization_id}, resource:[…]}, timestamp}`` webhook. To make the event
    correlatable the inject writes a fresh **invoice** (a new paid worker invoice)
    onto an existing contract and carries that full invoice object as the
    ``resource`` — so a consumer can fetch-on-notify by listing
    ``GET /rest/v2/invoices?status=all`` and matching the wire ``id``."""
    import secrets as _secrets

    from spammers.deel import dto as _deel_dto

    org = await pool.fetchrow(
        "SELECT id, organization_id FROM app_deel.organizations WHERE run_id=$1", run_id)
    if org is None:
        raise LookupError("no deel organization in this run; did you forget `prepare`?")
    contract = await pool.fetchrow(
        "SELECT id, contract_id, comp_amount_cents, comp_currency, worker_name "
        "FROM app_deel.contracts WHERE org_pk=$1 ORDER BY sort_key LIMIT 1", org["id"])
    if contract is None:
        raise LookupError("no deel contract for this org")

    clock = await get_clock(pool, run_id)
    when = at_virtual or clock.virtual_now
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    amount_cents = (int(round(amount_usd * 100)) if amount_usd is not None
                    else int(contract["comp_amount_cents"]))
    fee = max(500, amount_cents // 100)
    total = amount_cents + fee
    invoice_id = "inv_" + _secrets.token_hex(7)
    inv_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_deel.invoices
            (id, org_pk, contract_pk, invoice_id, contract_id, label, total_cents,
             amount_cents, vat_cents, deel_fee_cents, currency, status, issued_at,
             due_date, paid_at, created_at, is_overdue, recipient_legal_entity_id,
             sort_key, is_historical)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,0,$9,$10,'paid',$11,$11,$11,$11,FALSE,$12,$13,FALSE)""",
        inv_pk, org["id"], contract["id"], invoice_id, contract["contract_id"],
        f"INV-live {contract['worker_name']}", total, amount_cents, fee,
        contract["comp_currency"], when, "le_" + _secrets.token_hex(6),
        int(when.timestamp()))

    inv_row = await pool.fetchrow(
        "SELECT invoice_id, contract_id, label, total_cents, amount_cents, vat_cents, "
        "deel_fee_cents, currency, status, issued_at, due_date, paid_at, created_at, "
        "is_overdue, recipient_legal_entity_id FROM app_deel.invoices WHERE id=$1", inv_pk)
    payload = {"event_type": event_type, "resource": _deel_dto.invoice_dto(dict(inv_row))}

    actor = await pool.fetchval(
        "SELECT id FROM org.people WHERE run_id=$1 ORDER BY random() LIMIT 1", run_id)
    event_id = uuid4()
    await pool.execute(
        """INSERT INTO timeline.events
            (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical)
           VALUES ($1,$2,$3,'deel.event',$4,$5::jsonb,'{}'::jsonb,FALSE)""",
        event_id, run_id, when, actor, json.dumps(payload))
    return event_id


async def inject_figma_event(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    entity: str = "version",
    event_type: Optional[str] = None,
    at_virtual: Optional[datetime] = None,
) -> UUID:
    """Append a thin ``figma.event`` timeline event that drives the Figma
    **Webhooks-v2 body-passcode** delivery (NO HMAC — the passcode is a plaintext
    body field).

    Figma's live push is the merged version+comment event stream. Two correlatable
    shapes:

      * ``entity="version"`` → write a fresh **version** row on a file (new
        ``version_id`` > max) + emit ``FILE_VERSION_UPDATE`` with
        ``{version_id, file_key, file_name, triggered_by, created_at, description}``;
        a consumer fetches back via ``GET /v1/files/{key}/versions`` matching ``version_id``.
      * ``entity="comment"`` → write a fresh **comment** row + emit ``FILE_COMMENT``
        with ``{comment:[{text}], comment_id, file_key, file_name, triggered_by,
        created_at}``; a consumer fetches back via ``GET /v1/files/{key}/comments``
        matching ``comment_id``.
    """
    team = await pool.fetchrow(
        "SELECT id, team_id, base_url FROM app_figma.teams WHERE run_id=$1", run_id)
    if team is None:
        raise LookupError("no figma team in this run; did you forget `prepare`?")
    file_row = await pool.fetchrow(
        "SELECT id, file_key, name FROM app_figma.files WHERE team_pk=$1 "
        "ORDER BY sort_key LIMIT 1", team["id"])
    if file_row is None:
        raise LookupError("no figma file for this team")
    actor_user = await pool.fetchrow(
        "SELECT id, figma_user_id, handle, img_url FROM app_figma.users "
        "WHERE team_pk=$1 AND is_me=FALSE ORDER BY random() LIMIT 1", team["id"])
    if actor_user is None:
        actor_user = await pool.fetchrow(
            "SELECT id, figma_user_id, handle, img_url FROM app_figma.users "
            "WHERE team_pk=$1 LIMIT 1", team["id"])
    triggered_by = {"id": actor_user["figma_user_id"], "handle": actor_user["handle"],
                    "img_url": actor_user["img_url"]}

    clock = await get_clock(pool, run_id)
    when = at_virtual or clock.virtual_now
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    created_z = when.strftime("%Y-%m-%dT%H:%M:%SZ")

    if entity == "comment":
        import secrets as _secrets
        comment_id = str(_secrets.randbelow(900_000_000) + 9_000_000_000)
        await pool.execute(
            """INSERT INTO app_figma.comments
                (id, file_pk, comment_id, parent_id, user_pk, message, order_id,
                 client_meta, reactions, created_at, resolved_at, sort_key, is_historical)
               VALUES ($1,$2,$3,NULL,$4,$5,$6,$7::jsonb,'[]'::jsonb,$8,NULL,$9,FALSE)""",
            uuid4(), file_row["id"], comment_id, actor_user["id"],
            "Live review comment", "999999999",
            json.dumps({"x": 120.0, "y": 240.0}), when, int(when.timestamp()))
        payload = {
            "event_type": event_type or "FILE_COMMENT",
            "event": {
                "comment": [{"text": "Live review comment"}],
                "comment_id": comment_id,
                "created_at": created_z,
                "file_key": file_row["file_key"],
                "file_name": file_row["name"],
                "mentions": [],
                "triggered_by": triggered_by,
            },
        }
    else:
        seq = int(await pool.fetchval(
            "SELECT COALESCE(MAX(v.version_seq), 1100000000000) FROM app_figma.versions v "
            "JOIN app_figma.files f ON f.id = v.file_pk WHERE f.team_pk=$1",
            team["id"])) + 1
        version_id = str(seq)
        await pool.execute(
            """INSERT INTO app_figma.versions
                (id, file_pk, version_id, version_seq, label, description, user_pk,
                 created_at, is_historical)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,FALSE)""",
            uuid4(), file_row["id"], version_id, seq, "Live update",
            "Live update for " + file_row["name"], actor_user["id"], when)
        await pool.execute(
            "UPDATE app_figma.files SET current_version_id=$2, last_modified=$3 WHERE id=$1",
            file_row["id"], version_id, when)
        payload = {
            "event_type": event_type or "FILE_VERSION_UPDATE",
            "event": {
                "created_at": created_z,
                "description": "Live update for " + file_row["name"],
                "file_key": file_row["file_key"],
                "file_name": file_row["name"],
                "triggered_by": triggered_by,
                "version_id": version_id,
            },
        }

    actor = await pool.fetchval(
        "SELECT id FROM org.people WHERE run_id=$1 ORDER BY random() LIMIT 1", run_id)
    event_id = uuid4()
    await pool.execute(
        """INSERT INTO timeline.events
            (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical)
           VALUES ($1,$2,$3,'figma.event',$4,$5::jsonb,'{}'::jsonb,FALSE)""",
        event_id, run_id, when, actor, json.dumps(payload))
    return event_id


async def inject_hibob_event(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    entity: str = "employee",
    event_type: Optional[str] = None,
    at_virtual: Optional[datetime] = None,
) -> UUID:
    """Append a thin ``hibob.event`` timeline event that drives the signed HiBob
    **webhook** (``Bob-Signature`` base64 HMAC-SHA512 over the body).

    HiBob's live push channel is the **Webhooks v2** metadata-only envelope —
    ``{companyId, type, triggeredBy, triggeredAt, version, data}`` where ``data``
    carries IDs (NOT the full object). Two correlatable shapes:

      * ``entity="employee"`` → bump an existing employee's ``modified`` and emit
        ``employee.updated`` with ``data:{employeeId, fieldUpdatesIds:[…]}``; a
        consumer fetches back via ``POST /v1/people/search`` filtered on the id.
      * ``entity="timeoff"`` → write a fresh time-off CHANGE row and emit
        ``timeoff.request.approved`` with ``data:{timeoffRequestId, employeeId,
        getApi}``; a consumer fetches back via the ``/timeoff/requests/changes``
        feed and matches ``requestId``.
    """
    import secrets as _secrets
    co = await pool.fetchrow(
        "SELECT id, company_id, base_url FROM app_hibob.companies WHERE run_id=$1", run_id)
    if co is None:
        raise LookupError("no hibob company in this run; did you forget `prepare`?")

    clock = await get_clock(pool, run_id)
    when = at_virtual or clock.virtual_now
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    if entity == "timeoff":
        emp = await pool.fetchrow(
            "SELECT employee_id, full_name, email FROM app_hibob.employees "
            "WHERE company_pk=$1 AND is_active ORDER BY sort_key LIMIT 1", co["id"])
        if emp is None:
            raise LookupError("no active hibob employee for this company")
        request_id = _secrets.randbelow(9_000_000) + 9_000_000
        leave_start = (when + timedelta(days=10)).date()
        await pool.execute(
            """INSERT INTO app_hibob.timeoff_changes
                (id, company_pk, request_id, employee_id, employee_display_name,
                 employee_email, policy_type_display_name, change_type, status, created_on,
                 start_date, end_date, duration_unit, total_duration, total_cost,
                 request_type, sort_key, is_historical)
               VALUES ($1,$2,$3,$4,$5,$6,'Holiday','Created','approved',$7,$8,$9,'days',
                       2,2,'days',$10,FALSE)""",
            uuid4(), co["id"], request_id, emp["employee_id"], emp["full_name"],
            emp["email"], when, leave_start, leave_start + timedelta(days=1),
            int(when.timestamp()))
        payload = {
            "event_type": event_type or "timeoff.request.approved",
            "triggered_by": emp["employee_id"],
            "data": {"timeoffRequestId": request_id, "employeeId": emp["employee_id"],
                     "getApi": f"{co['base_url']}/v1/timeoff/requests/changes"},
        }
    else:
        emp = await pool.fetchrow(
            "SELECT id, employee_id FROM app_hibob.employees "
            "WHERE company_pk=$1 AND is_active ORDER BY random() LIMIT 1", co["id"])
        if emp is None:
            raise LookupError("no active hibob employee for this company")
        await pool.execute(
            "UPDATE app_hibob.employees SET modified=$2 WHERE id=$1", emp["id"], when)
        payload = {
            "event_type": event_type or "employee.updated",
            "triggered_by": emp["employee_id"],
            "data": {"employeeId": emp["employee_id"],
                     "fieldUpdatesIds": [{"id": "root.displayName"}]},
        }

    actor = await pool.fetchval(
        "SELECT id FROM org.people WHERE run_id=$1 ORDER BY random() LIMIT 1", run_id)
    event_id = uuid4()
    await pool.execute(
        """INSERT INTO timeline.events
            (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical)
           VALUES ($1,$2,$3,'hibob.event',$4,$5::jsonb,'{}'::jsonb,FALSE)""",
        event_id, run_id, when, actor, json.dumps(payload))
    return event_id


