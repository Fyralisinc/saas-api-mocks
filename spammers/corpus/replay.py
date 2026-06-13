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
    discord_bot_token, discord_snowflake,
    drive_file_id, gcal_event_id, gcal_ical_uid,
    github_app_id, github_installation_id, github_repo_id, github_user_id,
    github_webhook_secret,
    gmail_message_id, gmail_rfc822_id, gmail_thread_id,
    jira_account_id, jira_api_token, jira_cloud_id,
    notion_id, notion_token, notion_verification_token,
    rand_hex, seed_ids,
    slack_app_id, slack_bot_token, slack_channel_id, slack_client_id,
    slack_client_secret, slack_signing_secret, slack_team_id, slack_ts,
    slack_user_id,
)
from spammers.common.signing import generate_ed25519_keypair, generate_rsa_keypair
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


@register("org", "person.depart")
async def _org_person_depart(ctx: ReplayContext, event: Event) -> None:
    p = event["payload"]
    person_pk = await ctx.idmap.get(p["id"])
    if person_pk is None:
        return
    ended_at = _parse_ts(p.get("ended_at") or event["t"])
    await ctx.pool.execute(
        "UPDATE org.people SET ended_at = $2 WHERE id = $1",
        person_pk, ended_at,
    )


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
    is_im = bool(p.get("is_im"))
    is_mpim = bool(p.get("is_mpim"))
    # DMs/MPIMs are private by definition; channels honor the explicit flag.
    is_private = bool(p.get("is_private")) or is_im or is_mpim
    # Slack-style ID prefixes: D for IM, G for MPIM, C for channel.
    if is_im:
        slack_id = "D" + slack_channel_id()[1:]
    elif is_mpim:
        slack_id = "G" + slack_channel_id()[1:]
    else:
        slack_id = slack_channel_id()
    try:
        await ctx.pool.execute(
            "INSERT INTO app_slack.channels (id, workspace_id, channel_id, name, "
            "is_private, is_im, is_mpim, created_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
            pk, ws_pk, slack_id, p["name"][:80],
            is_private, is_im, is_mpim, when,
        )
        await ctx.idmap.put(p["id"], "slack_channel", pk)
    except asyncpg.exceptions.UniqueViolationError:
        # Channel name collisions across threads — append disambiguator
        await ctx.pool.execute(
            "INSERT INTO app_slack.channels (id, workspace_id, channel_id, name, "
            "is_private, is_im, is_mpim, created_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
            pk, ws_pk, slack_id, f"{p['name'][:75]}-{rand_hex(2)}",
            is_private, is_im, is_mpim, when,
        )
        await ctx.idmap.put(p["id"], "slack_channel", pk)

    # Seed channel_membership from the payload's explicit participants. A DM's
    # membership is intrinsic in real Slack: every participant sees the
    # conversation under their user token whether or not they ever posted. So we
    # persist it at creation rather than deriving it from who happened to author
    # a message (which would hide receive-only participants and mis-resolve an
    # im's `user` counterpart).
    for ref in p.get("participants") or []:
        try:
            member_pk = await _ensure_slack_user(ctx, ref)
        except KeyError:
            continue  # participant not in this run's people — skip, like messages do
        await ctx.pool.execute(
            "INSERT INTO app_slack.channel_membership (channel_pk, user_pk, joined_at) "
            "VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
            pk, member_pk, when,
        )


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
    """Gmail correspondence — one copy lands in each alpenlabs.io mailbox
    that appears in From or To, mirroring how a real Gmail tenant ingests:
    the sender sees their Sent copy, every internal recipient sees an Inbox
    copy. External addresses are dropped (we don't model investor mailboxes).

    Per-mailbox copies get distinct message_id values so the same logical
    event can land N times without UNIQUE violations. The `thread` payload
    key keys the thread so all copies stay correlated."""
    p = event["payload"]
    when = _parse_ts(event["t"])

    def _internal(addr: str) -> bool:
        return isinstance(addr, str) and addr.endswith("@alpenlabs.io")

    addrs: list[str] = []
    sender = p.get("from") or ""
    if _internal(sender):
        addrs.append(sender)
    for r in (p.get("to") or []):
        if _internal(r):
            addrs.append(r)
    # Dedupe while preserving order. If no alpenlabs.io address was found
    # (e.g. legacy fixtures), fall back to delbonis as the catch-all so we
    # never drop data on the floor.
    seen: set[str] = set()
    addrs = [a for a in addrs if not (a in seen or seen.add(a))]
    if not addrs:
        addrs = ["delbonis@alpenlabs.io"]

    thread_key = p.get("thread") or (p.get("subject", "") or "")[:40]
    rfc_id = gmail_rfc822_id("alpenlabs.io")
    headers = [
        {"name": "From",    "value": p.get("from", "")},
        {"name": "To",      "value": ", ".join(p.get("to") or [])},
        {"name": "Subject", "value": p.get("subject", "")},
        {"name": "Date",    "value": when.isoformat()},
        {"name": "Message-ID", "value": rfc_id},
    ]
    history_id = int(when.timestamp())

    for email in addrs:
        try:
            mailbox_pk = await _ensure_gmail_mailbox(ctx, email)
        except KeyError:
            continue
        thread_pk = await _ensure_gmail_thread(
            ctx, mailbox_pk, thread_key,
            p.get("subject", "(no subject)"),
        )
        gmid = gmail_message_id()
        try:
            await ctx.pool.execute(
                "INSERT INTO app_gmail.messages (id, thread_pk, message_id, history_id, "
                "rfc822_msg_id, headers, snippet, body_plain, internal_date) "
                "VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9)",
                uuid4(), thread_pk, gmid, history_id, rfc_id,
                json.dumps(headers),
                (p.get("body", "")[:200]),
                p.get("body", "")[:5000], when,
            )
        except asyncpg.exceptions.UniqueViolationError:
            pass


def _notion_rt(content: str) -> list:
    """A single plain-text rich-text run, Notion's documented shape."""
    return [{"type": "text", "text": {"content": content, "link": None},
             "annotations": {"bold": False, "italic": False, "strikethrough": False,
                             "underline": False, "code": False, "color": "default"},
             "plain_text": content, "href": None}]


# Structured page kinds live in real Notion *databases* (so each is a DB row with
# a workflow `Status` select → the consumer's `state_change` kind); free-form kinds
# (design_doc, rfc, retro, all_hands_recap, …) stay as loose workspace pages. This
# gives both backfill shard families — `notion_database` and `notion_page_tree` —
# real coverage from the corpus. kind → (database title, [status options]).
_NOTION_DB_KINDS = {
    "1on1_note":       ("1:1 Notes",  ["Scheduled", "In Progress", "Done"]),
    "hiring_decision": ("Hiring",     ["Sourced", "Onsite", "Decision", "Closed"]),
    "postmortem":      ("Incidents",  ["Investigating", "Mitigated", "Resolved"]),
}
_SELECT_COLORS = ["gray", "brown", "orange", "yellow", "green", "blue", "purple", "pink", "red"]


def _stable_idx(key: str, n: int) -> int:
    return sum(key.encode("utf-8")) % n if n else 0


def _select_color(name: str) -> str:
    return _SELECT_COLORS[_stable_idx(name, len(_SELECT_COLORS))]


async def _ensure_notion_database(ctx: ReplayContext, integ_pk: UUID, kind: str,
                                  title: str, status_opts: list[str], when: datetime) -> tuple:
    """Lazily create the per-kind database; returns (db_pk, database_id)."""
    key = f"notion:db:{kind}"
    db_pk = await ctx.idmap.get(key)
    if db_pk is not None:
        row = await ctx.pool.fetchrow("SELECT database_id FROM app_notion.databases WHERE id=$1", db_pk)
        return db_pk, row["database_id"]
    db_pk = uuid4()
    database_id = notion_id()
    schema = {
        "Name": {"id": "title", "name": "Name", "type": "title", "title": {}},
        "Status": {"id": "stat%3A", "name": "Status", "type": "select", "select": {
            "options": [{"id": notion_id()[:8], "name": o, "color": _select_color(o)}
                        for o in status_opts]}},
    }
    await ctx.pool.execute(
        "INSERT INTO app_notion.databases (id, integration_pk, database_id, title, "
        "parent_type, parent_id, icon, properties_schema, url, created_time, last_edited_time) "
        "VALUES ($1,$2,$3,$4,'workspace',NULL,NULL,$5::jsonb,$6,$7,$7)",
        db_pk, integ_pk, database_id, title, json.dumps(schema),
        f"https://www.notion.so/{database_id.replace('-', '')}", when,
    )
    await ctx.idmap.put(key, "notion_database", db_pk)
    return db_pk, database_id


@register("notion", "page.create")
async def _notion_page_create(ctx: ReplayContext, event: Event) -> None:
    p = event["payload"]
    when = _parse_ts(event["t"])
    integ_pk = await _ensure_notion_integration(ctx, when)
    bot_user_id = await ctx.pool.fetchval(
        "SELECT bot_user_id FROM app_notion.integrations WHERE id=$1", integ_pk)
    page_id = notion_id()
    title = (p.get("title") or "Untitled")[:300]
    kind = p.get("kind") or ""
    db_cfg = _NOTION_DB_KINDS.get(kind)
    pk = uuid4()
    try:
        if db_cfg:
            # Database row: typed property map = title + a workflow Status select.
            db_pk, database_id = await _ensure_notion_database(
                ctx, integ_pk, kind, db_cfg[0], db_cfg[1], when)
            status = db_cfg[1][_stable_idx(p["id"], len(db_cfg[1]))]
            props = {
                "Name": {"id": "title", "type": "title", "title": _notion_rt(title)},
                "Status": {"id": "stat%3A", "type": "select",
                           "select": {"name": status, "color": _select_color(status)}},
            }
            await ctx.pool.execute(
                "INSERT INTO app_notion.pages (id, integration_pk, page_id, parent_type, "
                "parent_id, database_pk, title, properties, created_by, url, created_time, last_edited_time) "
                "VALUES ($1,$2,$3,'database_id',$4,$5,$6,$7::jsonb,$8,$9,$10,$10)",
                pk, integ_pk, page_id, database_id, db_pk, title, json.dumps(props),
                bot_user_id, f"https://www.notion.so/{page_id.replace('-', '')}", when,
            )
            await ctx.pool.execute(
                "UPDATE app_notion.databases SET last_edited_time = GREATEST(last_edited_time, $2) "
                "WHERE id = $1", db_pk, when)
        else:
            # Loose page: properties = a single title entry; body lives in blocks.
            props = {"title": {"id": "title", "type": "title", "title": _notion_rt(title)}}
            await ctx.pool.execute(
                "INSERT INTO app_notion.pages (id, integration_pk, page_id, "
                "parent_type, title, properties, created_by, url, created_time, last_edited_time) "
                "VALUES ($1,$2,$3,'workspace',$4,$5::jsonb,$6,$7,$8,$8)",
                pk, integ_pk, page_id, title, json.dumps(props), bot_user_id,
                f"https://www.notion.so/{page_id.replace('-', '')}", when,
            )
        await ctx.idmap.put(p["id"], "notion_page", pk)
    except asyncpg.exceptions.UniqueViolationError:
        return
    # Body becomes a single paragraph block so the page has real, hydratable
    # content (GET /v1/blocks/{id}/children), mirroring how Notion stores text.
    body = (p.get("body_md") or "").strip()
    if body:
        await ctx.pool.execute(
            "INSERT INTO app_notion.blocks (id, page_pk, block_id, parent_block_id, "
            "type, content, has_children, position, created_by, created_time, last_edited_time) "
            "VALUES ($1,$2,$3,NULL,'paragraph',$4::jsonb,FALSE,0,$5,$6,$6)",
            uuid4(), pk, notion_id(),
            json.dumps({"rich_text": _notion_rt(body[:1800]), "color": "default"}),
            bot_user_id, when,
        )


@register("notion", "page.update")
async def _notion_page_update(ctx: ReplayContext, event: Event) -> None:
    """An edit bumps ``last_edited_time`` — the observable effect on the page
    object. (Notion exposes no edit-history list via the API.)"""
    p = event["payload"]
    page_pk = await ctx.idmap.get(p["id"])
    if page_pk is None:
        return
    when = _parse_ts(event["t"])
    await ctx.pool.execute(
        "UPDATE app_notion.pages SET last_edited_time = $2 WHERE id = $1",
        page_pk, when,
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
# discord.* handlers
# =============================================================================

async def _ensure_discord_application(ctx: ReplayContext, when: datetime) -> UUID:
    pk = await ctx.idmap.get("discord:app")
    if pk is not None:
        return pk
    pk = uuid4()
    priv, pub = generate_ed25519_keypair()
    await ctx.pool.execute(
        "INSERT INTO app_discord.applications (id, run_id, application_id, "
        "client_id, client_secret, bot_token, public_key, private_key) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
        pk, ctx.run_id, discord_snowflake(when), discord_snowflake(when),
        rand_hex(16), discord_bot_token(), pub, priv,
    )
    await ctx.idmap.put("discord:app", "discord_application", pk)
    return pk


async def _ensure_discord_guild(
    ctx: ReplayContext, corpus_guild_id: str, name: str, when: datetime,
) -> UUID:
    pk = await ctx.idmap.get(corpus_guild_id)
    if pk is not None:
        return pk
    app_pk = await _ensure_discord_application(ctx, when)
    pk = uuid4()
    await ctx.pool.execute(
        "INSERT INTO app_discord.guilds (id, application_pk, guild_id, name, "
        "owner_user_id, created_at) VALUES ($1,$2,$3,$4,$5,$6)",
        pk, app_pk, discord_snowflake(when), name,
        discord_snowflake(when), when,
    )
    await ctx.idmap.put(corpus_guild_id, "discord_guild", pk)
    return pk


async def _ensure_discord_user(ctx: ReplayContext, corpus_person_id: str) -> UUID | None:
    key = f"discord_user:{corpus_person_id}"
    pk = await ctx.idmap.get(key)
    if pk is not None:
        return pk
    person_pk = await ctx.idmap.get(corpus_person_id)
    if person_pk is None:
        return None
    person = await ctx.pool.fetchrow(
        "SELECT handle FROM org.people WHERE id = $1", person_pk,
    )
    app_pk = await _ensure_discord_application(ctx, datetime.now(timezone.utc))
    pk = uuid4()
    await ctx.pool.execute(
        "INSERT INTO app_discord.users (id, application_pk, person_id, "
        "discord_user_id, username) VALUES ($1,$2,$3,$4,$5) "
        "ON CONFLICT (application_pk, person_id) DO NOTHING",
        pk, app_pk, person_pk, discord_snowflake(), person["handle"],
    )
    real_pk = await ctx.pool.fetchval(
        "SELECT id FROM app_discord.users WHERE application_pk=$1 AND person_id=$2",
        app_pk, person_pk,
    )
    await ctx.idmap.put(key, "discord_user", real_pk)
    return real_pk


@register("discord", "channel.create")
async def _discord_channel_create(ctx: ReplayContext, event: Event) -> None:
    p = event["payload"]
    when = _parse_ts(event["t"])
    guild_corpus_id = p.get("guild") or "discord:guild:cross-org"
    guild_name = p.get("guild_name") or "ZK Research Roundup"
    guild_pk = await _ensure_discord_guild(ctx, guild_corpus_id, guild_name, when)
    pk = uuid4()
    try:
        await ctx.pool.execute(
            "INSERT INTO app_discord.channels (id, guild_pk, channel_id, name, "
            "type, topic, created_at) VALUES ($1,$2,$3,$4,$5,$6,$7)",
            pk, guild_pk, discord_snowflake(when), p["name"], 0,
            p.get("topic") or "", when,
        )
        await ctx.idmap.put(p["id"], "discord_channel", pk)
    except asyncpg.exceptions.UniqueViolationError:
        pass


@register("discord", "message")
async def _discord_message(ctx: ReplayContext, event: Event) -> None:
    p = event["payload"]
    when = _parse_ts(event["t"])
    channel_corpus_id = p.get("channel")
    if not channel_corpus_id:
        return
    channel_pk = await ctx.idmap.get(channel_corpus_id)
    if channel_pk is None:
        return
    author_pk = None
    actor = event.get("actor")
    if actor and actor.startswith("person:"):
        author_pk = await _ensure_discord_user(ctx, actor)
    try:
        await ctx.pool.execute(
            "INSERT INTO app_discord.messages (id, channel_pk, message_id, "
            "author_user_pk, content, type, created_at) "
            "VALUES ($1,$2,$3,$4,$5,0,$6)",
            uuid4(), channel_pk, discord_snowflake(when), author_pk,
            p.get("text") or p.get("content") or "", when,
        )
    except asyncpg.exceptions.UniqueViolationError:
        pass


# =============================================================================
# drive.* handlers
# =============================================================================

async def _ensure_drive_installation(ctx: ReplayContext) -> UUID:
    pk = await ctx.idmap.get("drive:installation")
    if pk is not None:
        return pk
    pk = uuid4()
    priv, pub = generate_rsa_keypair()
    await ctx.pool.execute(
        "INSERT INTO app_drive.installations (id, run_id, customer_id, domain, "
        "service_account_email, service_account_client_id, "
        "service_account_private_key, service_account_public_key) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
        pk, ctx.run_id, "C" + rand_hex(4), "alpenlabs.io",
        "ingest@alpenlabs-ingest.iam.gserviceaccount.com", rand_hex(10), priv, pub,
    )
    await ctx.idmap.put("drive:installation", "drive_installation", pk)
    return pk


async def _ensure_alpen_drive(ctx: ReplayContext) -> UUID:
    """One shared drive holds all corpus-generated files (PDFs, Google Docs,
    investor decks, audit reports, whitepapers). Real Drive installs would
    have a per-user 'my-drive' per oauth token, but the schema's
    (installation_pk, drive_id) UNIQUE makes us pick one — and a single
    shared drive matches how a small co. actually organizes docs."""
    key = "drive:alpen-shared"
    pk = await ctx.idmap.get(key)
    if pk is not None:
        return pk
    inst_pk = await _ensure_drive_installation(ctx)
    pk = uuid4()
    await ctx.pool.execute(
        "INSERT INTO app_drive.drives (id, installation_pk, drive_id, name, "
        "kind, owner_email, created_at) "
        "VALUES ($1,$2,'alpen-shared','Alpen Labs Drive','shared_drive',$3,$4)",
        pk, inst_pk, "drive-admin@alpenlabs.io", datetime.now(timezone.utc),
    )
    await ctx.idmap.put(key, "drive", pk)
    return pk


@register("drive", "file.create")
async def _drive_file_create(ctx: ReplayContext, event: Event) -> None:
    p = event["payload"]
    when = _parse_ts(event["t"])
    actor = event.get("actor")
    person_pk = None
    if actor and actor.startswith("person:"):
        person_pk = await ctx.idmap.get(actor)
    if person_pk is None:
        person_pk = await ctx.pool.fetchval(
            "SELECT id FROM org.people WHERE run_id=$1 ORDER BY started_at LIMIT 1",
            ctx.run_id,
        )
    if person_pk is None:
        return
    person = await ctx.pool.fetchrow(
        "SELECT email, full_name, handle FROM org.people WHERE id=$1", person_pk,
    )
    drive_pk = await _ensure_alpen_drive(ctx)
    inst_pk = await _ensure_drive_installation(ctx)
    file_id = drive_file_id()
    pk = uuid4()
    # change_seq is Drive's monotone change-tracking cursor (per installation).
    # Bump it by querying max+1 — fine for backfill volume (~tens of files).
    change_seq = (await ctx.pool.fetchval(
        "SELECT COALESCE(MAX(change_seq), 0) + 1 FROM app_drive.files "
        "WHERE installation_pk=$1", inst_pk,
    )) or 1
    try:
        await ctx.pool.execute(
            "INSERT INTO app_drive.files (id, installation_pk, drive_pk, file_id, "
            "name, mime_type, size, web_view_link, owner_email, owner_name, "
            "last_modifying_email, last_modifying_name, extracted_text, "
            "created_time, modified_time, change_seq) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$14,$15)",
            pk, inst_pk, drive_pk, file_id,
            p.get("name") or p.get("title") or "Untitled",
            p.get("mime_type") or "application/pdf",
            p.get("size") or 250_000,
            f"https://drive.google.com/file/d/{file_id}/view",
            person["email"], person["full_name"] or person["handle"],
            person["email"], person["full_name"] or person["handle"],
            p.get("body") or "",
            when, change_seq,
        )
        await ctx.idmap.put(p["id"], "drive_file", pk)
    except asyncpg.exceptions.UniqueViolationError:
        pass


# =============================================================================
# quickbooks.*
# =============================================================================

_QB_REALM_ID = "9341453412700001"   # stable mock realm id (single-tenant per run)


async def _ensure_qb_company(ctx: ReplayContext, name: str = "Alpen Labs",
                             when: datetime | None = None) -> UUID:
    """Lazy-create the QuickBooks company row for this run."""
    pk = await ctx.idmap.get("qb_company")
    if pk is not None:
        return pk
    row = await ctx.pool.fetchrow(
        "SELECT id FROM app_quickbooks.companies WHERE run_id=$1 AND realm_id=$2",
        ctx.run_id, _QB_REALM_ID,
    )
    if row is not None:
        await ctx.idmap.put("qb_company", "quickbooks_company", row["id"])
        return row["id"]
    pk = uuid4()
    await ctx.pool.execute(
        "INSERT INTO app_quickbooks.companies "
        "(id, run_id, realm_id, company_name, legal_name, country, currency, "
        " fiscal_year_start, created_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
        pk, ctx.run_id, _QB_REALM_ID,
        name, "Alpen Labs, Inc.", "US", "USD", "January",
        when or datetime.now(timezone.utc),
    )
    await ctx.idmap.put("qb_company", "quickbooks_company", pk)
    return pk


@register("quickbooks", "company.create")
async def _qb_company_create(ctx: ReplayContext, event: Event) -> None:
    p = event["payload"]
    when = _parse_ts(event["t"])
    pk = await _ensure_qb_company(ctx, name=p.get("company_name", "Alpen Labs"), when=when)
    # If the renderer supplied a non-default realm_id, prefer it (otherwise
    # leave the one set during _ensure_qb_company).
    realm_id = p.get("realm_id")
    if realm_id and realm_id != _QB_REALM_ID:
        await ctx.pool.execute(
            "UPDATE app_quickbooks.companies SET realm_id=$1 WHERE id=$2",
            realm_id, pk,
        )


@register("quickbooks", "account.create")
async def _qb_account_create(ctx: ReplayContext, event: Event) -> None:
    p = event["payload"]
    when = _parse_ts(event["t"])
    company_pk = await _ensure_qb_company(ctx, when=when)
    acct_pk = uuid4()
    try:
        await ctx.pool.execute(
            "INSERT INTO app_quickbooks.accounts "
            "(id, company_pk, account_id, account_number, name, type, subtype, "
            " description, currency, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
            acct_pk, company_pk, p["id"], p["number"], p["name"],
            p["type"], p["subtype"], p.get("description", ""),
            p.get("currency", "USD"), when,
        )
        await ctx.idmap.put(f"qb_acct:{p['number']}", "quickbooks_account", acct_pk)
    except asyncpg.exceptions.UniqueViolationError:
        # Already created — idempotent reseed.
        pk = await ctx.pool.fetchval(
            "SELECT id FROM app_quickbooks.accounts WHERE company_pk=$1 AND account_number=$2",
            company_pk, p["number"],
        )
        if pk:
            await ctx.idmap.put(f"qb_acct:{p['number']}", "quickbooks_account", pk)


async def _qb_account_pk(ctx: ReplayContext, company_pk: UUID, number: str) -> UUID | None:
    pk = await ctx.idmap.get(f"qb_acct:{number}")
    if pk is not None:
        return pk
    pk = await ctx.pool.fetchval(
        "SELECT id FROM app_quickbooks.accounts WHERE company_pk=$1 AND account_number=$2",
        company_pk, number,
    )
    if pk:
        await ctx.idmap.put(f"qb_acct:{number}", "quickbooks_account", pk)
    return pk


@register("quickbooks", "vendor.create")
async def _qb_vendor_create(ctx: ReplayContext, event: Event) -> None:
    p = event["payload"]
    when = _parse_ts(event["t"])
    company_pk = await _ensure_qb_company(ctx, when=when)
    vendor_pk = uuid4()
    try:
        await ctx.pool.execute(
            "INSERT INTO app_quickbooks.vendors "
            "(id, company_pk, vendor_id, display_name, active, currency, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7)",
            vendor_pk, company_pk, p["id"], p["display_name"],
            p.get("active", True), p.get("currency", "USD"), when,
        )
        await ctx.idmap.put(f"qb_vendor:{p['id']}", "quickbooks_vendor", vendor_pk)
    except asyncpg.exceptions.UniqueViolationError:
        pk = await ctx.pool.fetchval(
            "SELECT id FROM app_quickbooks.vendors WHERE company_pk=$1 AND vendor_id=$2",
            company_pk, p["id"],
        )
        if pk:
            await ctx.idmap.put(f"qb_vendor:{p['id']}", "quickbooks_vendor", pk)


@register("quickbooks", "employee.create")
async def _qb_employee_create(ctx: ReplayContext, event: Event) -> None:
    p = event["payload"]
    when = _parse_ts(event["t"])
    company_pk = await _ensure_qb_company(ctx, when=when)
    person_pk = None
    actor = event.get("actor")
    if actor and actor.startswith("person:"):
        person_pk = await ctx.idmap.get(actor)
    emp_pk = uuid4()
    try:
        await ctx.pool.execute(
            "INSERT INTO app_quickbooks.employees "
            "(id, company_pk, employee_id, person_id, display_name, email, title, "
            " team, location_bucket, annual_salary_cents, active, hired_at, "
            " released_at, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)",
            emp_pk, company_pk, p["id"], person_pk,
            p["display_name"], p.get("email"),
            p.get("title", ""), p.get("team", ""),
            p.get("location_bucket", "other"),
            int(p["annual_salary_usd"]) * 100,
            p.get("active", True),
            datetime.fromisoformat(p["hired_at"]).date(),
            datetime.fromisoformat(p["released_at"]).date() if p.get("released_at") else None,
            when,
        )
        await ctx.idmap.put(f"qb_emp:{p['id']}", "quickbooks_employee", emp_pk)
    except asyncpg.exceptions.UniqueViolationError:
        pk = await ctx.pool.fetchval(
            "SELECT id FROM app_quickbooks.employees WHERE company_pk=$1 AND employee_id=$2",
            company_pk, p["id"],
        )
        if pk:
            await ctx.idmap.put(f"qb_emp:{p['id']}", "quickbooks_employee", pk)


@register("quickbooks", "deposit")
async def _qb_deposit(ctx: ReplayContext, event: Event) -> None:
    p = event["payload"]
    when = _parse_ts(event["t"])
    company_pk = await _ensure_qb_company(ctx, when=when)
    deposit_to = await _qb_account_pk(ctx, company_pk, p.get("deposit_to_account", "1000"))
    credit     = await _qb_account_pk(ctx, company_pk, p.get("credit_account", "3000"))
    pk = uuid4()
    try:
        await ctx.pool.execute(
            "INSERT INTO app_quickbooks.deposits "
            "(id, company_pk, deposit_id, txn_date, amount_cents, "
            " deposit_to_account_pk, credit_account_pk, round_id, round_kind, "
            " lead, participants, memo, created_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12,$13)",
            pk, company_pk, p["id"],
            datetime.fromisoformat(p["txn_date"]).date(),
            int(p["amount_usd"]) * 100,
            deposit_to, credit,
            p.get("round_id"), p.get("round_kind"),
            p.get("lead", ""),
            json.dumps(p.get("participants", [])),
            p.get("memo", ""),
            when,
        )
        # Running balance: debit bank, credit equity/income.
        amount_cents = int(p["amount_usd"]) * 100
        if deposit_to:
            await ctx.pool.execute(
                "UPDATE app_quickbooks.accounts SET current_balance_cents = "
                "current_balance_cents + $1 WHERE id = $2", amount_cents, deposit_to)
        if credit:
            await ctx.pool.execute(
                "UPDATE app_quickbooks.accounts SET current_balance_cents = "
                "current_balance_cents - $1 WHERE id = $2", amount_cents, credit)
    except asyncpg.exceptions.UniqueViolationError:
        pass


@register("quickbooks", "purchase")
async def _qb_purchase(ctx: ReplayContext, event: Event) -> None:
    p = event["payload"]
    when = _parse_ts(event["t"])
    company_pk = await _ensure_qb_company(ctx, when=when)
    vendor_pk = await ctx.idmap.get(f"qb_vendor:{p.get('vendor_id', '')}")
    employee_pk = None
    if p.get("person_id"):
        employee_pk = await ctx.idmap.get(f"qb_emp:emp-{_short_seed(p['person_id'])}")
        # Fall back: look up by person_id directly.
        if employee_pk is None:
            employee_pk = await ctx.pool.fetchval(
                "SELECT e.id FROM app_quickbooks.employees e "
                "JOIN org.people p ON p.id = e.person_id "
                "WHERE e.company_pk = $1 AND p.handle = ANY (ARRAY[$2, $3])",
                company_pk, p["person_id"].removeprefix("person:"), p["person_id"],
            )
    expense_pk = await _qb_account_pk(ctx, company_pk, p.get("expense_account", "5000"))
    payment_pk = await _qb_account_pk(ctx, company_pk, p.get("payment_account", "1000"))
    pk = uuid4()
    try:
        await ctx.pool.execute(
            "INSERT INTO app_quickbooks.purchases "
            "(id, company_pk, purchase_id, txn_date, amount_cents, vendor_pk, "
            " employee_pk, expense_account_pk, payment_account_pk, category, "
            " memo, payload, created_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13)",
            pk, company_pk, p["id"],
            datetime.fromisoformat(p["txn_date"]).date(),
            int(p["amount_usd"]) * 100,
            vendor_pk, employee_pk, expense_pk, payment_pk,
            p.get("category", "other"),
            p.get("memo", ""),
            json.dumps({k: v for k, v in p.items()
                        if k not in ("id", "txn_date", "amount_usd", "vendor",
                                     "vendor_id", "expense_account", "payment_account",
                                     "category", "memo", "person_id")}),
            when,
        )
        # Running balance: debit expense, credit bank.
        amount_cents = int(p["amount_usd"]) * 100
        if expense_pk:
            await ctx.pool.execute(
                "UPDATE app_quickbooks.accounts SET current_balance_cents = "
                "current_balance_cents + $1 WHERE id = $2", amount_cents, expense_pk)
        if payment_pk:
            await ctx.pool.execute(
                "UPDATE app_quickbooks.accounts SET current_balance_cents = "
                "current_balance_cents - $1 WHERE id = $2", amount_cents, payment_pk)
    except asyncpg.exceptions.UniqueViolationError:
        pass


def _short_seed(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()[:12]


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
