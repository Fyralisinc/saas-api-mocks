"""Read the current run's state and inject new activity.

All functions take an asyncpg pool. Counts/people are plain reads; the
inject helpers write into the served tables so the new content is visible
over the mocks' REST APIs immediately (no emission loop needed).
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

import asyncpg

from spammers.common.ids import slack_ts, discord_snowflake
from spammers.orggen.live import inject_github_event


# --------------------------------------------------------------------------- run

async def current_run_id(pool: asyncpg.Pool) -> Optional[UUID]:
    row = await pool.fetchrow("SELECT id FROM org.runs ORDER BY created_at DESC LIMIT 1")
    return row["id"] if row else None


# --------------------------------------------------------------------------- status

async def provider_status(pool: asyncpg.Pool, run_id: UUID) -> dict:
    """Per-provider counts + people for the current run."""
    people = await pool.fetchval("SELECT count(*) FROM org.people WHERE run_id=$1", run_id)
    teams = await pool.fetchval("SELECT count(*) FROM org.teams WHERE run_id=$1", run_id)

    # Slack
    ws = await pool.fetchrow("SELECT id, team_name, team_id FROM app_slack.workspaces WHERE run_id=$1", run_id)
    slack = {"channels": 0, "messages": 0, "team": None}
    if ws:
        slack["team"] = ws["team_name"]
        slack["channels"] = await pool.fetchval(
            "SELECT count(*) FROM app_slack.channels WHERE workspace_id=$1", ws["id"])
        slack["messages"] = await pool.fetchval(
            "SELECT count(*) FROM app_slack.messages m JOIN app_slack.channels c ON c.id=m.channel_pk "
            "WHERE c.workspace_id=$1", ws["id"])

    # Discord
    dapp = await pool.fetchrow("SELECT id FROM app_discord.applications WHERE run_id=$1", run_id)
    discord = {"channels": 0, "messages": 0, "guild": None}
    if dapp:
        g = await pool.fetchrow("SELECT id, name FROM app_discord.guilds WHERE application_pk=$1", dapp["id"])
        if g:
            discord["guild"] = g["name"]
            discord["channels"] = await pool.fetchval(
                "SELECT count(*) FROM app_discord.channels WHERE guild_pk=$1", g["id"])
            discord["messages"] = await pool.fetchval(
                "SELECT count(*) FROM app_discord.messages msg JOIN app_discord.channels ch ON ch.id=msg.channel_pk "
                "WHERE ch.guild_pk=$1", g["id"])

    # GitHub
    app = await pool.fetchrow("SELECT id FROM app_github.apps WHERE run_id=$1", run_id)
    github = {"repos": 0, "issues": 0, "pull_requests": 0, "commits": 0}
    if app:
        inst = await pool.fetchrow("SELECT id FROM app_github.installations WHERE app_pk=$1", app["id"])
        if inst:
            repos = await pool.fetch("SELECT id FROM app_github.repositories WHERE installation_pk=$1", inst["id"])
            ids = [r["id"] for r in repos]
            github["repos"] = len(ids)
            if ids:
                github["issues"] = await pool.fetchval("SELECT count(*) FROM app_github.issues WHERE repo_pk=ANY($1)", ids)
                github["pull_requests"] = await pool.fetchval("SELECT count(*) FROM app_github.pull_requests WHERE repo_pk=ANY($1)", ids)
                github["commits"] = await pool.fetchval("SELECT count(*) FROM app_github.commits WHERE repo_pk=ANY($1)", ids)

    return {"people": people, "teams": teams, "slack": slack, "discord": discord, "github": github}


# --------------------------------------------------------------------------- people / channels / repos

async def list_people(pool: asyncpg.Pool, run_id: UUID) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT p.handle, p.full_name, p.role, p.level, t.name AS team
          FROM org.people p LEFT JOIN org.teams t ON t.id = p.team_id
         WHERE p.run_id = $1 ORDER BY t.name NULLS LAST, p.handle
        """, run_id)
    return [dict(r) for r in rows]


async def list_channels(pool: asyncpg.Pool, run_id: UUID, provider: str) -> list[str]:
    if provider == "slack":
        ws = await pool.fetchrow("SELECT id FROM app_slack.workspaces WHERE run_id=$1", run_id)
        if not ws:
            return []
        rows = await pool.fetch("SELECT name FROM app_slack.channels WHERE workspace_id=$1 ORDER BY name", ws["id"])
        return [r["name"] for r in rows]
    if provider == "discord":
        dapp = await pool.fetchrow("SELECT id FROM app_discord.applications WHERE run_id=$1", run_id)
        if not dapp:
            return []
        g = await pool.fetchrow("SELECT id FROM app_discord.guilds WHERE application_pk=$1", dapp["id"])
        if not g:
            return []
        rows = await pool.fetch("SELECT name FROM app_discord.channels WHERE guild_pk=$1 AND type=0 ORDER BY name", g["id"])
        return [r["name"] for r in rows]
    return []


async def list_repos(pool: asyncpg.Pool, run_id: UUID) -> list[str]:
    app = await pool.fetchrow("SELECT id FROM app_github.apps WHERE run_id=$1", run_id)
    if not app:
        return []
    inst = await pool.fetchrow("SELECT id FROM app_github.installations WHERE app_pk=$1", app["id"])
    if not inst:
        return []
    rows = await pool.fetch("SELECT name FROM app_github.repositories WHERE installation_pk=$1 ORDER BY name", inst["id"])
    return [r["name"] for r in rows]


# --------------------------------------------------------------------------- suggestions

_ENG_ROLES = {"ic", "senior", "staff", "manager", "head", "cto"}
_SALES_ROLES = {"ae", "ae_senior"}


async def suggestions(pool: asyncpg.Pool, run_id: UUID, handle: str, provider: str) -> list[str]:
    person = await pool.fetchrow(
        """SELECT p.full_name, p.role, t.name AS team
             FROM org.people p LEFT JOIN org.teams t ON t.id=p.team_id
            WHERE p.run_id=$1 AND p.handle=$2""", run_id, handle)
    if not person:
        return []
    # a project for context (owned, else any)
    proj = await pool.fetchrow(
        """SELECT pr.title, pr.slug, pr.repos
             FROM org.projects pr JOIN org.people pe ON pe.id=pr.owner_id
            WHERE pr.run_id=$1 AND pe.handle=$2 LIMIT 1""", run_id, handle)
    if proj is None:
        proj = await pool.fetchrow("SELECT title, slug, repos FROM org.projects WHERE run_id=$1 ORDER BY random() LIMIT 1", run_id)
    title = proj["title"] if proj else "the project"
    slug = proj["slug"] if proj else "project"
    repos = (proj["repos"] if proj else None) or [slug]
    repo = repos[0] if isinstance(repos, list) and repos else slug

    role = (person["role"] or "").lower()
    team = (person["team"] or "")
    is_eng = role in _ENG_ROLES or "Engineering" in team
    is_sales = role in _SALES_ROLES or team == "Sales"

    if provider == "github":
        # github inject creates an issue → suggest issue titles
        return [
            f"{slug}: intermittent timeouts under load",
            f"{slug} crashes on empty payload",
            f"{slug} returns stale data after deploy",
            f"Investigate flaky tests in {slug}",
            f"{title}: tracking issue",
        ]

    if is_sales:
        base = [
            "closed the Northwind deal 🎉",
            "demo went well — they want a follow-up next week",
            f"prospect is asking about {title}, can eng confirm the timeline?",
            "pipeline review at 2pm, numbers in the deck",
            "lost the Acme renewal — writing up the post-mortem",
        ]
    elif is_eng:
        base = [
            f"good morning! yesterday: wrapped up {title} — today: reviews + on-call",
            f"PR up for review: {repo} — would love eyes before EOD",
            f"{slug} is flaky under load again, digging into it now",
            f"merged the {title} fix, deploying to staging",
            f"anyone know if the {slug} timeout is configurable?",
        ]
    else:
        base = [
            f"shared the latest {title} update in the doc",
            "standup in 5",
            f"can someone unblock the {slug} review?",
            "shipped a small fix",
            f"quick sync on {title} this afternoon?",
        ]
    if provider == "discord":
        base = base + ["brb coffee ☕", "lol the build is red again"]
    return base[:6]


# --------------------------------------------------------------------------- inject

async def inject_slack(pool: asyncpg.Pool, run_id: UUID, *, handle: str, channel: str, text: str) -> dict:
    ws = await pool.fetchrow("SELECT id, team_id FROM app_slack.workspaces WHERE run_id=$1", run_id)
    if not ws:
        raise ValueError("no slack workspace for this run")
    chan = channel.lstrip("#")
    crow = await pool.fetchrow(
        "SELECT id, channel_id FROM app_slack.channels WHERE workspace_id=$1 AND (name=$2 OR channel_id=$2)",
        ws["id"], chan)
    if not crow:
        raise ValueError(f"unknown slack channel: {channel}")
    urow = await pool.fetchrow(
        "SELECT u.id, u.slack_user_id FROM app_slack.users u JOIN org.people p ON p.id=u.person_id "
        "WHERE u.workspace_id=$1 AND p.handle=$2", ws["id"], handle)
    if not urow:
        raise ValueError(f"unknown person: {handle}")
    # unique (channel_pk, ts) — retry with a tiny bump on collision
    for bump in range(8):
        ts = slack_ts(datetime.now(timezone.utc))
        if bump:
            base, frac = ts.split(".")
            ts = f"{base}.{int(frac) + bump:06d}"
        try:
            await pool.execute(
                """INSERT INTO app_slack.messages (id, channel_pk, user_pk, ts, text, is_hidden)
                   VALUES ($1,$2,$3,$4,$5,FALSE)""",
                uuid4(), crow["id"], urow["id"], ts, text)
            return {"provider": "slack", "channel": f"#{crow['channel_id']}", "ts": ts, "user": urow["slack_user_id"]}
        except asyncpg.UniqueViolationError:
            continue
    raise RuntimeError("could not allocate a unique slack ts")


async def inject_discord(pool: asyncpg.Pool, run_id: UUID, *, handle: str, channel: str, text: str) -> dict:
    dapp = await pool.fetchrow("SELECT id FROM app_discord.applications WHERE run_id=$1", run_id)
    if not dapp:
        raise ValueError("no discord application for this run")
    g = await pool.fetchrow("SELECT id FROM app_discord.guilds WHERE application_pk=$1", dapp["id"])
    if not g:
        raise ValueError("no discord guild for this run")
    chan = channel.lstrip("#")
    crow = await pool.fetchrow(
        "SELECT id, channel_id FROM app_discord.channels WHERE guild_pk=$1 AND (name=$2 OR channel_id=$2)",
        g["id"], chan)
    if not crow:
        raise ValueError(f"unknown discord channel: {channel}")
    urow = await pool.fetchrow(
        "SELECT u.id, u.discord_user_id FROM app_discord.users u JOIN org.people p ON p.id=u.person_id "
        "WHERE u.application_pk=$1 AND p.handle=$2", dapp["id"], handle)
    if not urow:
        raise ValueError(f"unknown person: {handle}")
    mid = discord_snowflake(datetime.now(timezone.utc))
    await pool.execute(
        """INSERT INTO app_discord.messages (id, channel_pk, message_id, author_user_pk, content, type, created_at)
           VALUES ($1,$2,$3,$4,$5,0,$6)""",
        uuid4(), crow["id"], mid, urow["id"], text, datetime.now(timezone.utc))
    return {"provider": "discord", "channel": crow["channel_id"], "message_id": mid, "author": urow["discord_user_id"]}


async def inject_github(pool: asyncpg.Pool, run_id: UUID, *, handle: str, repo: str, text: str) -> dict:
    """A GitHub 'message' = a newly opened issue (title=text), by the actor."""
    repos = await list_repos(pool, run_id)
    if repo not in repos:
        if not repos:
            raise ValueError("no github repos for this run")
        repo = repos[0]
    event_id = await inject_github_event(pool, run_id, kind="issues", repo=repo, handle=handle, title=text)
    return {"provider": "github", "repo": repo, "event_id": str(event_id)}
