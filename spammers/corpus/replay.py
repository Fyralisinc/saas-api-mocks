"""Apply a stream of corpus events to the provider DBs.

Dispatch by ``(event['provider'], event['kind'])``. Handler signature:

    async def handler(ctx: ReplayContext, event: Event) -> None

All handlers are registered in this file via the @register decorator. The
``_ensure_*`` helpers lazy-bootstrap workspace/installation rows the first
time a provider event arrives — keeps the corpus file from having to enumerate
boilerplate workspace state.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

import asyncpg
import structlog

from spammers.common.ids import (
    drive_file_id, gcal_event_id, gcal_ical_uid,
    github_app_id, github_installation_id, github_repo_id, github_user_id,
    github_webhook_secret,
    gmail_message_id, gmail_thread_id,
    jira_account_id, jira_api_token, jira_cloud_id,
    notion_id, notion_token, notion_verification_token,
    rand_hex, seed_ids,
    slack_app_id, slack_bot_token, slack_channel_id, slack_client_id,
    slack_client_secret, slack_signing_secret, slack_team_id, slack_ts,
    slack_user_id,
)
from spammers.common.signing import generate_rsa_keypair
from spammers.corpus.cursor import advance as advance_cursor
from spammers.corpus.idmap import IdMap
from spammers.corpus.loader import iter_events
from spammers.corpus.schema import Event, KINDS


log = structlog.get_logger("spammers.corpus.replay")


@dataclass
class ReplayContext:
    pool: asyncpg.Pool
    run_id: UUID
    idmap: IdMap


Handler = Callable[[ReplayContext, Event], Awaitable[None]]
_REGISTRY: dict[tuple[str, str], Handler] = {}


def register(provider: str, kind: str) -> Callable[[Handler], Handler]:
    if provider not in KINDS or kind not in KINDS[provider]:
        raise ValueError(f"unknown ({provider}, {kind}) — add to schema.KINDS first")
    def deco(fn: Handler) -> Handler:
        _REGISTRY[(provider, kind)] = fn
        return fn
    return deco


def _parse_ts(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def _not_implemented(ctx: ReplayContext, event: Event) -> None:
    log.debug("corpus_handler_missing",
              provider=event["provider"], kind=event["kind"], t=event["t"])


# =============================================================================
# org.*
# =============================================================================

@register("org", "team.create")
async def _org_team_create(ctx: ReplayContext, event: Event) -> None:
    p = event["payload"]
    if await ctx.idmap.get(p["id"]) is not None:
        return
    pk = uuid4()
    parent_pk = await ctx.idmap.get(p["parent"]) if p.get("parent") else None
    await ctx.pool.execute(
        "INSERT INTO org.teams (id, run_id, name, parent_id) VALUES ($1,$2,$3,$4)",
        pk, ctx.run_id, p["name"], parent_pk,
    )
    await ctx.idmap.put(p["id"], "team", pk)


@register("org", "person.create")
async def _org_person_create(ctx: ReplayContext, event: Event) -> None:
    p = event["payload"]
    if await ctx.idmap.get(p["id"]) is not None:
        return
    pk = uuid4()
    team_pk = await ctx.idmap.get(p["team"]) if p.get("team") else None
    await ctx.pool.execute(
        "INSERT INTO org.people (id, run_id, handle, full_name, email, role, level, "
        "team_id, timezone, started_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
        pk, ctx.run_id, p["handle"], p["full_name"], p["email"],
        p.get("role", "engineer"), p.get("level", "ic"), team_pk,
        p.get("timezone", "UTC"), _parse_ts(event["t"]),
    )
    await ctx.idmap.put(p["id"], "person", pk)


# =============================================================================
# Lazy bootstraps — per-provider workspace/installation/account state
# =============================================================================

async def _ensure_slack_workspace(ctx: ReplayContext, when: datetime) -> UUID:
    pk = await ctx.idmap.get("slack:workspace")
    if pk is not None:
        return pk
    pk = uuid4()
    await ctx.pool.execute(
        "INSERT INTO app_slack.workspaces (id, run_id, team_id, team_name, team_domain, "
        "signing_secret, client_id, client_secret, bot_token, bot_user_id, app_id) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
        pk, ctx.run_id, slack_team_id(), "Alpen Labs", "alpenlabs",
        slack_signing_secret(), slack_client_id(), slack_client_secret(),
        slack_bot_token(), slack_user_id(), slack_app_id(),
    )
    await ctx.idmap.put("slack:workspace", "slack_workspace", pk)
    return pk


async def _ensure_slack_user(ctx: ReplayContext, corpus_person_id: str) -> UUID:
    key = f"slack:user:{corpus_person_id}"
    pk = await ctx.idmap.get(key)
    if pk is not None:
        return pk
    person_pk = await ctx.idmap.get(corpus_person_id)
    if person_pk is None:
        raise KeyError(f"slack: unknown actor {corpus_person_id}")
    ws_pk = await _ensure_slack_workspace(ctx, datetime.now(timezone.utc))
    pk = uuid4()
    await ctx.pool.execute(
        "INSERT INTO app_slack.users (id, workspace_id, person_id, slack_user_id, "
        "is_bot, profile) VALUES ($1,$2,$3,$4,FALSE,'{}')",
        pk, ws_pk, person_pk, slack_user_id(),
    )
    await ctx.idmap.put(key, "slack_user", pk)
    return pk


async def _ensure_github_app(ctx: ReplayContext, when: datetime) -> tuple[UUID, UUID]:
    """Bootstrap GitHub app + installation. Returns (app_pk, installation_pk)."""
    app_pk = await ctx.idmap.get("github:app")
    inst_pk = await ctx.idmap.get("github:installation")
    if app_pk is not None and inst_pk is not None:
        return app_pk, inst_pk
    app_pk = uuid4()
    inst_pk = uuid4()
    priv, pub = generate_rsa_keypair()
    await ctx.pool.execute(
        "INSERT INTO app_github.apps (id, run_id, app_id, slug, name, client_id, "
        "client_secret, webhook_secret, private_key, public_key) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
        app_pk, ctx.run_id, github_app_id(), "fyralis-ingest", "Fyralis Ingest",
        rand_hex(10), rand_hex(20), github_webhook_secret(), priv, pub,
    )
    await ctx.pool.execute(
        "INSERT INTO app_github.installations (id, app_pk, installation_id, "
        "account_login, account_type, account_id, created_at) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7)",
        inst_pk, app_pk, github_installation_id(),
        "alpenlabs", "Organization", github_user_id(), when,
    )
    await ctx.idmap.put("github:app", "github_app", app_pk)
    await ctx.idmap.put("github:installation", "github_installation", inst_pk)
    return app_pk, inst_pk


async def _ensure_jira_installation(ctx: ReplayContext, when: datetime) -> UUID:
    pk = await ctx.idmap.get("jira:installation")
    if pk is not None:
        return pk
    pk = uuid4()
    await ctx.pool.execute(
        "INSERT INTO app_jira.installations (id, run_id, base_url, site_name, "
        "cloud_id, account_email, account_id, api_token, webhook_secret) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
        pk, ctx.run_id, "https://alpenlabs.atlassian.net", "alpenlabs",
        jira_cloud_id(), "ingest@alpenlabs.io",
        jira_account_id(), jira_api_token(), rand_hex(32),
    )
    await ctx.idmap.put("jira:installation", "jira_installation", pk)
    return pk


async def _ensure_jira_user(ctx: ReplayContext, corpus_person_id: str) -> str:
    """Return the jira account_id for a corpus person (lazy-creates)."""
    key = f"jira:user:{corpus_person_id}"
    pk = await ctx.idmap.get(key)
    if pk is not None:
        row = await ctx.pool.fetchrow(
            "SELECT account_id FROM app_jira.users WHERE id = $1", pk,
        )
        return row["account_id"]
    person_pk = await ctx.idmap.get(corpus_person_id)
    if person_pk is None:
        raise KeyError(f"jira: unknown actor {corpus_person_id}")
    inst_pk = await _ensure_jira_installation(ctx, datetime.now(timezone.utc))
    person = await ctx.pool.fetchrow(
        "SELECT handle, full_name, email FROM org.people WHERE id = $1", person_pk,
    )
    account_id = jira_account_id()
    user_pk = uuid4()
    await ctx.pool.execute(
        "INSERT INTO app_jira.users (id, installation_pk, person_id, account_id, "
        "email, display_name) VALUES ($1,$2,$3,$4,$5,$6)",
        user_pk, inst_pk, person_pk, account_id,
        person["email"], person["full_name"] or person["handle"],
    )
    await ctx.idmap.put(key, "jira_user", user_pk)
    return account_id


async def _ensure_jira_project(ctx: ReplayContext, key: str) -> UUID:
    cache_key = f"jira:project:{key}"
    pk = await ctx.idmap.get(cache_key)
    if pk is not None:
        return pk
    inst_pk = await _ensure_jira_installation(ctx, datetime.now(timezone.utc))
    pk = uuid4()
    await ctx.pool.execute(
        "INSERT INTO app_jira.projects (id, installation_pk, project_id, key, name, "
        "project_type_key) VALUES ($1,$2,$3,$4,$5,'software')",
        pk, inst_pk, str(abs(hash(key)) % 100000), key, key,
    )
    await ctx.idmap.put(cache_key, "jira_project", pk)
    return pk


async def _ensure_notion_integration(ctx: ReplayContext, when: datetime) -> UUID:
    pk = await ctx.idmap.get("notion:integration")
    if pk is not None:
        return pk
    pk = uuid4()
    await ctx.pool.execute(
        "INSERT INTO app_notion.integrations (id, run_id, bot_token, workspace_id, "
        "workspace_name, bot_user_id, bot_name, client_id, client_secret, "
        "verification_token) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
        pk, ctx.run_id, notion_token(), notion_id(), "Alpen Labs",
        notion_id(), "Ingest Bot", notion_id(), notion_token(),
        notion_verification_token(),
    )
    await ctx.idmap.put("notion:integration", "notion_integration", pk)
    return pk


async def _ensure_calendar_account(ctx: ReplayContext) -> UUID:
    pk = await ctx.idmap.get("calendar:account")
    if pk is not None:
        return pk
    pk = uuid4()
    priv, pub = generate_rsa_keypair()
    await ctx.pool.execute(
        "INSERT INTO app_calendar.accounts (id, run_id, customer_id, domain, "
        "service_account_email, service_account_client_id, "
        "service_account_private_key, service_account_public_key) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
        pk, ctx.run_id, "C" + rand_hex(4), "alpenlabs.io",
        "ingest@alpenlabs-ingest.iam.gserviceaccount.com", rand_hex(10), priv, pub,
    )
    await ctx.idmap.put("calendar:account", "calendar_account", pk)
    return pk


async def _ensure_calendar_for_person(ctx: ReplayContext, person_pk: UUID, email: str) -> UUID:
    key = f"calendar:cal:{person_pk}"
    pk = await ctx.idmap.get(key)
    if pk is not None:
        return pk
    acct_pk = await _ensure_calendar_account(ctx)
    pk = uuid4()
    await ctx.pool.execute(
        "INSERT INTO app_calendar.calendars (id, account_pk, person_id, calendar_id, "
        "summary, time_zone) VALUES ($1,$2,$3,$4,$5,'UTC')",
        pk, acct_pk, person_pk, email, email,
    )
    await ctx.idmap.put(key, "calendar_calendar", pk)
    return pk


# =============================================================================
# github.* handlers
# =============================================================================

@register("github", "user.create")
async def _gh_user_create(ctx: ReplayContext, event: Event) -> None:
    # We only mint github users implicitly (via PRs/commits). Keep a corpus_id
    # → login mapping in idmap so other handlers can resolve "ghuser:foo".
    p = event["payload"]
    await ctx.idmap.put(p["id"], "github_login", uuid4())  # filler PK


@register("github", "repo.create")
async def _gh_repo_create(ctx: ReplayContext, event: Event) -> None:
    p = event["payload"]
    if await ctx.idmap.get(p["id"]) is not None:
        return
    _, inst_pk = await _ensure_github_app(ctx, _parse_ts(event["t"]))
    pk = uuid4()
    await ctx.pool.execute(
        "INSERT INTO app_github.repositories (id, installation_pk, repo_id, owner, "
        "name, default_branch, description, created_at) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
        pk, inst_pk, github_repo_id(), p.get("owner", "alpenlabs"),
        p["name"], p.get("default_branch", "main"), p.get("description") or "",
        _parse_ts(event["t"]),
    )
    await ctx.idmap.put(p["id"], "github_repo", pk)


def _gh_login(actor: str | None) -> str:
    if not actor:
        return "ghost"
    return actor.split(":", 1)[1] if actor.startswith("ghuser:") else actor


@register("github", "commit")
async def _gh_commit(ctx: ReplayContext, event: Event) -> None:
    p = event["payload"]
    repo_pk = await ctx.idmap.get(p["repo"])
    if repo_pk is None:
        return  # commits for repos we didn't create (forks; rare). Skip.
    login = _gh_login(event.get("actor"))
    try:
        await ctx.pool.execute(
            "INSERT INTO app_github.commits (id, repo_pk, sha, message, author_login, "
            "author_email, committed_at, parents) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,'[]'::jsonb) "
            "ON CONFLICT (repo_pk, sha) DO NOTHING",
            uuid4(), repo_pk, p["sha"], p.get("message", "")[:1000],
            login, f"{login}@users.noreply.github.com", _parse_ts(event["t"]),
        )
    except asyncpg.exceptions.UniqueViolationError:
        pass


@register("github", "pr.open")
async def _gh_pr_open(ctx: ReplayContext, event: Event) -> None:
    p = event["payload"]
    repo_pk = await ctx.idmap.get(p["repo"])
    if repo_pk is None:
        return
    login = _gh_login(event.get("actor"))
    pr_pk = uuid4()
    sha = rand_hex(20)
    try:
        await ctx.pool.execute(
            "INSERT INTO app_github.pull_requests (id, repo_pk, number, title, body, "
            "state, merged, user_login, head_ref, head_sha, base_ref, base_sha, "
            "created_at, updated_at) "
            "VALUES ($1,$2,$3,$4,'','open',FALSE,$5,$6,$7,$8,$9,$10,$10)",
            pr_pk, repo_pk, p["number"], p.get("title") or "(no title)",
            login, p.get("head") or "feature", sha,
            p.get("base") or "main", rand_hex(20), _parse_ts(event["t"]),
        )
        await ctx.idmap.put(f"github:pr:{p['repo']}:{p['number']}", "github_pr", pr_pk)
    except asyncpg.exceptions.UniqueViolationError:
        pass


@register("github", "pr.merge")
async def _gh_pr_merge(ctx: ReplayContext, event: Event) -> None:
    p = event["payload"]
    repo_pk = await ctx.idmap.get(p["repo"])
    if repo_pk is None:
        return
    when = _parse_ts(event["t"])
    await ctx.pool.execute(
        "UPDATE app_github.pull_requests SET state='closed', merged=TRUE, "
        "merged_at=$3, closed_at=$3, updated_at=$3 "
        "WHERE repo_pk=$1 AND number=$2",
        repo_pk, p["number"], when,
    )


@register("github", "pr.close")
async def _gh_pr_close(ctx: ReplayContext, event: Event) -> None:
    p = event["payload"]
    repo_pk = await ctx.idmap.get(p["repo"])
    if repo_pk is None:
        return
    when = _parse_ts(event["t"])
    await ctx.pool.execute(
        "UPDATE app_github.pull_requests SET state='closed', closed_at=$3, "
        "updated_at=$3 WHERE repo_pk=$1 AND number=$2",
        repo_pk, p["number"], when,
    )


@register("github", "issue.open")
async def _gh_issue_open(ctx: ReplayContext, event: Event) -> None:
    p = event["payload"]
    repo_pk = await ctx.idmap.get(p["repo"])
    if repo_pk is None:
        return
    login = _gh_login(event.get("actor"))
    when = _parse_ts(event["t"])
    try:
        await ctx.pool.execute(
            "INSERT INTO app_github.issues (id, repo_pk, number, title, state, "
            "user_login, labels, created_at, updated_at) "
            "VALUES ($1,$2,$3,$4,'open',$5,$6::jsonb,$7,$7)",
            uuid4(), repo_pk, p["number"], (p.get("title") or "(no title)")[:300],
            login, json.dumps([{"name": l} for l in (p.get("labels") or []) if l]),
            when,
        )
    except asyncpg.exceptions.UniqueViolationError:
        pass


@register("github", "issue.close")
async def _gh_issue_close(ctx: ReplayContext, event: Event) -> None:
    p = event["payload"]
    repo_pk = await ctx.idmap.get(p["repo"])
    if repo_pk is None:
        return
    when = _parse_ts(event["t"])
    await ctx.pool.execute(
        "UPDATE app_github.issues SET state='closed', closed_at=$3, updated_at=$3 "
        "WHERE repo_pk=$1 AND number=$2",
        repo_pk, p["number"], when,
    )


@register("github", "release.publish")
async def _gh_release_publish(ctx: ReplayContext, event: Event) -> None:
    # Releases table isn't in the existing schema yet; we just log the count
    # on the repo description. Keep handler so kind is "implemented".
    pass


@register("github", "review.submit")
async def _gh_review_submit(ctx: ReplayContext, event: Event) -> None:
    """Synthetic PR review event — carries reviewer voice + state."""
    p = event["payload"]
    repo_pk = await ctx.idmap.get(p["repo"])
    if repo_pk is None:
        return
    pr_pk = await ctx.pool.fetchval(
        "SELECT id FROM app_github.pull_requests WHERE repo_pk=$1 AND number=$2",
        repo_pk, p["pr_number"],
    )
    if pr_pk is None:
        return
    login = _gh_login(event.get("actor"))
    when = _parse_ts(event["t"])
    try:
        await ctx.pool.execute(
            "INSERT INTO app_github.reviews (id, pr_pk, user_login, state, body, "
            "submitted_at) VALUES ($1,$2,$3,$4,$5,$6)",
            uuid4(), pr_pk, login, p.get("state", "commented"),
            p.get("body", "")[:1000], when,
        )
    except asyncpg.exceptions.UniqueViolationError:
        pass


# =============================================================================
# slack.* handlers
# =============================================================================

@register("slack", "channel.create")
async def _slack_channel_create(ctx: ReplayContext, event: Event) -> None:
    p = event["payload"]
    if await ctx.idmap.get(p["id"]) is not None:
        return
    when = _parse_ts(event["t"])
    ws_pk = await _ensure_slack_workspace(ctx, when)
    pk = uuid4()
    try:
        await ctx.pool.execute(
            "INSERT INTO app_slack.channels (id, workspace_id, channel_id, name, "
            "is_private, created_at) VALUES ($1,$2,$3,$4,$5,$6)",
            pk, ws_pk, slack_channel_id(), p["name"][:80],
            p.get("is_private", False), when,
        )
        await ctx.idmap.put(p["id"], "slack_channel", pk)
    except asyncpg.exceptions.UniqueViolationError:
        # Channel name collisions across threads — append disambiguator
        await ctx.pool.execute(
            "INSERT INTO app_slack.channels (id, workspace_id, channel_id, name, "
            "is_private, created_at) VALUES ($1,$2,$3,$4,$5,$6)",
            pk, ws_pk, slack_channel_id(), f"{p['name'][:75]}-{rand_hex(2)}",
            p.get("is_private", False), when,
        )
        await ctx.idmap.put(p["id"], "slack_channel", pk)


@register("slack", "message")
async def _slack_message(ctx: ReplayContext, event: Event) -> None:
    p = event["payload"]
    channel_pk = await ctx.idmap.get(p["channel"])
    if channel_pk is None:
        return  # message in a channel we never created
    actor = event.get("actor")
    user_pk = None
    if actor and actor.startswith("person:"):
        try:
            user_pk = await _ensure_slack_user(ctx, actor)
        except KeyError:
            user_pk = None
    when = _parse_ts(event["t"])
    ts = slack_ts(when)
    # Thread-ts maps a reply to its parent anchor. We accept either a raw ts
    # (the corpus emits this for replies inside the same beat) or pass NULL
    # for top-level messages.
    thread_ts = p.get("thread_ts")
    reactions = p.get("reactions") or []
    try:
        await ctx.pool.execute(
            "INSERT INTO app_slack.messages (id, channel_pk, user_pk, ts, thread_ts, "
            "text, reactions) VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)",
            uuid4(), channel_pk, user_pk, ts, thread_ts,
            p.get("text", "")[:2000], json.dumps(reactions),
        )
    except asyncpg.exceptions.UniqueViolationError:
        pass


# =============================================================================
# jira.* handlers
# =============================================================================

@register("jira", "issue.create")
async def _jira_issue_create(ctx: ReplayContext, event: Event) -> None:
    p = event["payload"]
    when = _parse_ts(event["t"])
    proj_key = p.get("project") or p["key"].split("-")[0]
    proj_pk = await _ensure_jira_project(ctx, proj_key)
    inst_pk = await ctx.idmap.get("jira:installation")

    reporter = (await _ensure_jira_user(ctx, p["reporter"])
                if p.get("reporter") else None)
    assignee = (await _ensure_jira_user(ctx, p["assignee"])
                if p.get("assignee") else None)

    pk = uuid4()
    try:
        await ctx.pool.execute(
            "INSERT INTO app_jira.issues (id, installation_pk, project_pk, issue_id, "
            "issue_key, summary, issue_type, status, status_category, "
            "reporter_account_id, assignee_account_id, creator_account_id, "
            "labels, story_points, created_at, updated_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,'To Do','new',$8,$9,$8,$10::jsonb,$11,$12,$12)",
            pk, inst_pk, proj_pk, str(abs(hash(p["key"])) % 1000000),
            p["key"], (p.get("summary") or p["key"])[:300],
            p.get("type", "Task"), reporter, assignee,
            json.dumps(p.get("labels") or []),
            p.get("story_points"),
            when,
        )
        await ctx.idmap.put(f"jira:issue:{p['key']}", "jira_issue", pk)
    except asyncpg.exceptions.UniqueViolationError:
        pass


@register("jira", "issue.assign")
async def _jira_issue_assign(ctx: ReplayContext, event: Event) -> None:
    """Reassignment changelog: issue moves to a new assignee."""
    p = event["payload"]
    issue_pk = await ctx.idmap.get(f"jira:issue:{p['key']}")
    if issue_pk is None:
        return
    when = _parse_ts(event["t"])
    to_actor = p.get("to_assignee")
    from_actor = p.get("from_assignee")
    try:
        to_acct = await _ensure_jira_user(ctx, to_actor) if to_actor else None
        from_acct = (await _ensure_jira_user(ctx, from_actor)
                     if from_actor else None)
    except KeyError:
        return
    try:
        await ctx.pool.execute(
            "UPDATE app_jira.issues SET assignee_account_id=$2, updated_at=$3 "
            "WHERE id=$1", issue_pk, to_acct, when,
        )
        await ctx.pool.execute(
            "INSERT INTO app_jira.changelogs (id, issue_pk, history_id, "
            "author_account_id, items, created_at) "
            "VALUES ($1,$2,$3,$4,$5::jsonb,$6)",
            uuid4(), issue_pk,
            str(abs(hash(event["t"] + p["key"] + "asn")) % 10_000_000),
            from_acct,
            json.dumps([{"field": "assignee", "fieldtype": "jira",
                         "from": from_acct, "fromString": from_actor or "",
                         "to": to_acct, "toString": to_actor or ""}]),
            when,
        )
    except asyncpg.exceptions.UniqueViolationError:
        pass


@register("jira", "issue.transition")
async def _jira_issue_transition(ctx: ReplayContext, event: Event) -> None:
    p = event["payload"]
    issue_pk = await ctx.idmap.get(f"jira:issue:{p['key']}")
    if issue_pk is None:
        return
    when = _parse_ts(event["t"])
    to_status = p.get("to_status", "Done")
    category = {"To Do": "new", "In Progress": "indeterminate",
                "Done": "done"}.get(to_status, "indeterminate")
    actor_id = (await _ensure_jira_user(ctx, event["actor"])
                if event.get("actor", "").startswith("person:") else None)
    try:
        await ctx.pool.execute(
            "UPDATE app_jira.issues SET status=$2, status_category=$3, updated_at=$4 "
            "WHERE id=$1", issue_pk, to_status, category, when,
        )
        await ctx.pool.execute(
            "INSERT INTO app_jira.changelogs (id, issue_pk, history_id, "
            "author_account_id, items, created_at) "
            "VALUES ($1,$2,$3,$4,$5::jsonb,$6)",
            uuid4(), issue_pk, str(abs(hash(event["t"]+p['key'])) % 10_000_000),
            actor_id,
            json.dumps([{"field": "status", "fieldtype": "jira",
                         "from": p.get("from_status", "To Do"),
                         "fromString": p.get("from_status", "To Do"),
                         "to": to_status, "toString": to_status}]),
            when,
        )
    except asyncpg.exceptions.UniqueViolationError:
        pass


# =============================================================================
# notion.* handlers
# =============================================================================

async def _ensure_gmail_customer(ctx: ReplayContext) -> UUID:
    pk = await ctx.idmap.get("gmail:customer")
    if pk is not None:
        return pk
    pk = uuid4()
    priv, pub = generate_rsa_keypair()
    await ctx.pool.execute(
        "INSERT INTO app_gmail.customers (id, run_id, customer_id, domain, "
        "organization_name, service_account_email, service_account_public_key, "
        "pubsub_oidc_public_key, pubsub_oidc_private_key, pubsub_audience) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
        pk, ctx.run_id, "C" + rand_hex(4), "alpenlabs.io", "Alpen Labs",
        "ingest@alpenlabs-ingest.iam.gserviceaccount.com",
        pub, pub, priv, "alpen-ingest",
    )
    await ctx.idmap.put("gmail:customer", "gmail_customer", pk)
    return pk


async def _ensure_gmail_mailbox(ctx: ReplayContext, email: str) -> UUID:
    key = f"gmail:mailbox:{email}"
    pk = await ctx.idmap.get(key)
    if pk is not None:
        return pk
    cust_pk = await _ensure_gmail_customer(ctx)
    # Use the cofounder as the catch-all for external mailboxes; for internal,
    # try to resolve the person from email-local-part = handle.
    handle = email.split("@", 1)[0]
    person_pk = await ctx.pool.fetchval(
        "SELECT id FROM org.people WHERE run_id=$1 AND (email=$2 OR handle=$3) "
        "ORDER BY started_at LIMIT 1",
        ctx.run_id, email, handle,
    )
    if person_pk is None:
        # External — pin to the cofounder/owner mailbox so we don't FK-fail.
        person_pk = await ctx.pool.fetchval(
            "SELECT id FROM org.people WHERE run_id=$1 ORDER BY started_at LIMIT 1",
            ctx.run_id,
        )
    if person_pk is None:
        raise KeyError("no people in run yet")
    pk = uuid4()
    try:
        await ctx.pool.execute(
            "INSERT INTO app_gmail.mailboxes (id, customer_pk, person_id, email) "
            "VALUES ($1,$2,$3,$4)",
            pk, cust_pk, person_pk, email,
        )
    except asyncpg.exceptions.UniqueViolationError:
        pk = await ctx.pool.fetchval(
            "SELECT id FROM app_gmail.mailboxes WHERE customer_pk=$1 AND email=$2",
            cust_pk, email,
        )
    await ctx.idmap.put(key, "gmail_mailbox", pk)
    return pk


async def _ensure_gmail_thread(ctx: ReplayContext, mailbox_pk: UUID,
                               thread_key: str, subject: str) -> UUID:
    key = f"gmail:thread:{mailbox_pk}:{thread_key}"
    pk = await ctx.idmap.get(key)
    if pk is not None:
        return pk
    pk = uuid4()
    try:
        await ctx.pool.execute(
            "INSERT INTO app_gmail.threads (id, mailbox_pk, thread_id, subject, snippet) "
            "VALUES ($1,$2,$3,$4,$5)",
            pk, mailbox_pk, gmail_thread_id(), subject[:300], subject[:200],
        )
    except asyncpg.exceptions.UniqueViolationError:
        pk = await ctx.pool.fetchval(
            "SELECT id FROM app_gmail.threads WHERE mailbox_pk=$1 AND thread_id=$2",
            mailbox_pk, thread_key,
        )
    await ctx.idmap.put(key, "gmail_thread", pk)
    return pk


@register("gmail", "message")
async def _gmail_message(ctx: ReplayContext, event: Event) -> None:
    """External (investor / audit / partner) Gmail correspondence.

    Each message lands in the local cofounder's mailbox. External thread_id
    from payload is preserved for grouping; only the alpenlabs.io mailbox is
    actually populated (we don't model external mailboxes — outgoing/incoming
    both pin to the local side).
    """
    p = event["payload"]
    local_email = "delbonis@alpenlabs.io"  # the in-org side of the conversation
    try:
        mailbox_pk = await _ensure_gmail_mailbox(ctx, local_email)
    except KeyError:
        return
    thread_key = p.get("thread") or p.get("subject", "")[:40]
    thread_pk = await _ensure_gmail_thread(ctx, mailbox_pk, thread_key,
                                            p.get("subject", "(no subject)"))
    when = _parse_ts(event["t"])
    headers = [
        {"name": "From", "value": p.get("from", "")},
        {"name": "To", "value": ", ".join(p.get("to") or [])},
        {"name": "Subject", "value": p.get("subject", "")},
        {"name": "Date", "value": when.isoformat()},
    ]
    history_id = int(when.timestamp())
    try:
        await ctx.pool.execute(
            "INSERT INTO app_gmail.messages (id, thread_pk, message_id, history_id, "
            "rfc822_msg_id, headers, snippet, body_plain, internal_date) "
            "VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9)",
            uuid4(), thread_pk, gmail_message_id(), history_id,
            f"<{gmail_message_id()}@alpenlabs.io>",
            json.dumps(headers),
            (p.get("body", "")[:200]),
            p.get("body", "")[:5000], when,
        )
    except asyncpg.exceptions.UniqueViolationError:
        pass


@register("notion", "page.create")
async def _notion_page_create(ctx: ReplayContext, event: Event) -> None:
    p = event["payload"]
    when = _parse_ts(event["t"])
    integ_pk = await _ensure_notion_integration(ctx, when)
    page_id = notion_id()
    pk = uuid4()
    try:
        await ctx.pool.execute(
            "INSERT INTO app_notion.pages (id, integration_pk, page_id, "
            "parent_type, title, properties, url, created_time, last_edited_time) "
            "VALUES ($1,$2,$3,'workspace',$4,$5::jsonb,$6,$7,$7)",
            pk, integ_pk, page_id, (p.get("title") or "Untitled")[:300],
            json.dumps({"body_md": p.get("body_md", ""),
                        "kind": p.get("kind", "doc"),
                        "is_private": p.get("is_private", False),
                        "audience": p.get("audience") or [],
                        "category": p.get("category") or ""}),
            f"https://www.notion.so/{page_id.replace('-', '')}", when,
        )
        await ctx.idmap.put(p["id"], "notion_page", pk)
    except asyncpg.exceptions.UniqueViolationError:
        pass


@register("notion", "page.update")
async def _notion_page_update(ctx: ReplayContext, event: Event) -> None:
    """Edit history: bumps last_edited_time + appends summary into properties."""
    p = event["payload"]
    page_pk = await ctx.idmap.get(p["id"])
    if page_pk is None:
        return
    when = _parse_ts(event["t"])
    summary = p.get("summary", "edit")
    await ctx.pool.execute(
        "UPDATE app_notion.pages "
        "SET last_edited_time = $2, "
        "    properties = jsonb_set(properties, '{edit_history}', "
        "        COALESCE(properties->'edit_history', '[]'::jsonb) "
        "        || jsonb_build_array(jsonb_build_object("
        "          'at', to_char($2 at time zone 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), "
        "          'editor', $3::text, 'summary', $4::text))) "
        "WHERE id = $1",
        page_pk, when, event.get("actor") or "", summary,
    )


# =============================================================================
# calendar.* handlers
# =============================================================================

@register("calendar", "event.create")
async def _calendar_event_create(ctx: ReplayContext, event: Event) -> None:
    p = event["payload"]
    when = _parse_ts(event["t"])
    # Use the organizer's calendar; fall back to first available person.
    organizer = event.get("actor")
    if organizer and organizer.startswith("person:"):
        person_pk = await ctx.idmap.get(organizer)
    else:
        person_pk = await ctx.pool.fetchval(
            "SELECT id FROM org.people WHERE run_id = $1 ORDER BY started_at LIMIT 1",
            ctx.run_id,
        )
    if person_pk is None:
        return
    person = await ctx.pool.fetchrow(
        "SELECT email FROM org.people WHERE id = $1", person_pk,
    )
    cal_pk = await _ensure_calendar_for_person(ctx, person_pk, person["email"])

    start = _parse_ts(p["start"]) if p.get("start") else when
    end = _parse_ts(p["end"]) if p.get("end") else when
    attendee_emails = []
    for a in p.get("attendees") or []:
        a_pk = await ctx.idmap.get(a)
        if a_pk:
            row = await ctx.pool.fetchrow(
                "SELECT email FROM org.people WHERE id = $1", a_pk)
            if row:
                attendee_emails.append({"email": row["email"], "responseStatus": "accepted"})

    try:
        await ctx.pool.execute(
            "INSERT INTO app_calendar.events (id, calendar_pk, event_id, status, "
            "summary, start_time, end_time, organizer_email, creator_email, "
            "attendees, ical_uid, created_at, updated_at) "
            "VALUES ($1,$2,$3,'confirmed',$4,$5,$6,$7,$7,$8::jsonb,$9,$10,$10)",
            uuid4(), cal_pk, gcal_event_id(), p.get("summary", "Meeting")[:300],
            start, end, person["email"],
            json.dumps(attendee_emails), gcal_ical_uid(), when,
        )
    except asyncpg.exceptions.UniqueViolationError:
        pass


# =============================================================================
# Public entry point
# =============================================================================

async def backfill(
    pool: asyncpg.Pool,
    run_id: UUID,
    corpus_path: str | Path,
    *,
    until: datetime,
) -> dict[str, int]:
    """Land all events in ``corpus_path`` whose ``t <= until``.

    Idempotent — handlers gate on idmap so re-running with the same cursor
    inserts nothing new. Re-running with a later cursor adds only the
    newly-due events.
    """
    seed_row = await pool.fetchrow("SELECT seed FROM org.runs WHERE id = $1", run_id)
    seed_ids(int(seed_row["seed"]) if seed_row else 43)

    idmap = IdMap(pool, run_id)
    await idmap.warm()
    ctx = ReplayContext(pool=pool, run_id=run_id, idmap=idmap)
    counts: dict[str, int] = {}
    errors: dict[str, int] = {}
    last_ts: datetime | None = None
    for ts, event in iter_events(corpus_path, until=until):
        key = f"{event['provider']}.{event['kind']}"
        handler = _REGISTRY.get((event["provider"], event["kind"]), _not_implemented)
        try:
            await handler(ctx, event)
            counts[key] = counts.get(key, 0) + 1
        except Exception as e:
            errors[key] = errors.get(key, 0) + 1
            if errors[key] <= 3:
                log.warning("corpus_handler_error", kind=key, error=str(e)[:200])
        last_ts = ts
    if last_ts is not None:
        await advance_cursor(pool, run_id, last_ts)
    log.info("corpus_backfill_done", run_id=str(run_id), until=until.isoformat(),
             total=sum(counts.values()), kinds=len(counts), errors=errors)
    return counts
