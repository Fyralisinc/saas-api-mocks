"""Read the current run's state and inject new activity.

All functions take an asyncpg pool. Counts/people are plain reads; the
inject helpers write into the served tables so the new content is visible
over the mocks' REST APIs immediately (no emission loop needed).
"""
from __future__ import annotations

import json
import os
import random
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

import asyncpg

from spammers.common.ids import (
    slack_ts, discord_snowflake, gcal_event_id, gcal_ical_uid,
    gmail_message_id, gmail_thread_id, notion_id,
)
from spammers.orggen.live import (
    inject_github_event,
    inject_drive_file as _inject_drive_file,
    inject_jira_issue as _inject_jira_issue,
)


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

    # Google Calendar
    cal_acct = await pool.fetchrow("SELECT id FROM app_calendar.accounts WHERE run_id=$1", run_id)
    calendar = {"calendars": 0, "events": 0}
    if cal_acct:
        calendar["calendars"] = await pool.fetchval(
            "SELECT count(*) FROM app_calendar.calendars WHERE account_pk=$1", cal_acct["id"])
        calendar["events"] = await pool.fetchval(
            "SELECT count(*) FROM app_calendar.events e JOIN app_calendar.calendars c ON c.id=e.calendar_pk "
            "WHERE c.account_pk=$1", cal_acct["id"])

    # Notion
    integ = await pool.fetchrow("SELECT id FROM app_notion.integrations WHERE run_id=$1", run_id)
    notion = {"databases": 0, "pages": 0, "comments": 0}
    if integ:
        notion["databases"] = await pool.fetchval(
            "SELECT count(*) FROM app_notion.databases WHERE integration_pk=$1", integ["id"])
        notion["pages"] = await pool.fetchval(
            "SELECT count(*) FROM app_notion.pages WHERE integration_pk=$1", integ["id"])
        notion["comments"] = await pool.fetchval(
            "SELECT count(*) FROM app_notion.comments c JOIN app_notion.pages p ON p.id=c.page_pk "
            "WHERE p.integration_pk=$1", integ["id"])

    # Gmail
    cust = await pool.fetchrow("SELECT id FROM app_gmail.customers WHERE run_id=$1", run_id)
    gmail = {"mailboxes": 0, "threads": 0, "messages": 0}
    if cust:
        mboxes = await pool.fetch("SELECT id FROM app_gmail.mailboxes WHERE customer_pk=$1", cust["id"])
        mids = [m["id"] for m in mboxes]
        gmail["mailboxes"] = len(mids)
        if mids:
            gmail["threads"] = await pool.fetchval(
                "SELECT count(*) FROM app_gmail.threads WHERE mailbox_pk=ANY($1)", mids)
            gmail["messages"] = await pool.fetchval(
                "SELECT count(*) FROM app_gmail.messages m JOIN app_gmail.threads t ON t.id=m.thread_pk "
                "WHERE t.mailbox_pk=ANY($1)", mids)

    # Google Drive
    dinst = await pool.fetchrow("SELECT id FROM app_drive.installations WHERE run_id=$1", run_id)
    drive = {"drives": 0, "files": 0, "comments": 0}
    if dinst:
        drive["drives"] = await pool.fetchval(
            "SELECT count(*) FROM app_drive.drives WHERE installation_pk=$1", dinst["id"])
        drive["files"] = await pool.fetchval(
            "SELECT count(*) FROM app_drive.files WHERE installation_pk=$1", dinst["id"])
        drive["comments"] = await pool.fetchval(
            "SELECT count(*) FROM app_drive.comments c JOIN app_drive.files f ON f.id=c.file_pk "
            "WHERE f.installation_pk=$1", dinst["id"])

    # Jira
    jinst = await pool.fetchrow("SELECT id FROM app_jira.installations WHERE run_id=$1", run_id)
    jira = {"projects": 0, "issues": 0, "comments": 0}
    if jinst:
        jira["projects"] = await pool.fetchval(
            "SELECT count(*) FROM app_jira.projects WHERE installation_pk=$1", jinst["id"])
        jira["issues"] = await pool.fetchval(
            "SELECT count(*) FROM app_jira.issues WHERE installation_pk=$1", jinst["id"])
        jira["comments"] = await pool.fetchval(
            "SELECT count(*) FROM app_jira.comments c JOIN app_jira.issues i ON i.id=c.issue_pk "
            "WHERE i.installation_pk=$1", jinst["id"])

    # QuickBooks
    qcomp = await pool.fetchrow(
        "SELECT id FROM app_quickbooks.companies WHERE run_id=$1", run_id)
    quickbooks = {"employees": 0, "deposits": 0, "purchases": 0,
                  "bank_balance_cents": 0, "total_raised_cents": 0,
                  "total_spent_cents": 0}
    if qcomp:
        quickbooks["employees"] = await pool.fetchval(
            "SELECT count(*) FROM app_quickbooks.employees WHERE company_pk=$1", qcomp["id"])
        quickbooks["deposits"] = await pool.fetchval(
            "SELECT count(*) FROM app_quickbooks.deposits WHERE company_pk=$1", qcomp["id"])
        quickbooks["purchases"] = await pool.fetchval(
            "SELECT count(*) FROM app_quickbooks.purchases WHERE company_pk=$1", qcomp["id"])
        # Bank balance is the Operating Bank account (number 1000) — the
        # running balance reflects all deposits in + purchases out so far.
        quickbooks["bank_balance_cents"] = int(await pool.fetchval(
            "SELECT COALESCE(current_balance_cents, 0) FROM app_quickbooks.accounts "
            "WHERE company_pk=$1 AND account_number='1000'", qcomp["id"]) or 0)
        quickbooks["total_raised_cents"] = int(await pool.fetchval(
            "SELECT COALESCE(sum(amount_cents), 0) FROM app_quickbooks.deposits "
            "WHERE company_pk=$1", qcomp["id"]) or 0)
        quickbooks["total_spent_cents"] = int(await pool.fetchval(
            "SELECT COALESCE(sum(amount_cents), 0) FROM app_quickbooks.purchases "
            "WHERE company_pk=$1", qcomp["id"]) or 0)

    return {"people": people, "teams": teams, "slack": slack, "discord": discord,
            "github": github, "calendar": calendar, "notion": notion, "gmail": gmail,
            "drive": drive, "jira": jira, "quickbooks": quickbooks}


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

    if provider == "gmail":
        # gmail inject sends an email → first line is the subject
        return [
            f"Re: {title} rollout plan\n\nLooping you in — can you confirm the timeline?",
            f"Question about {slug}\n\nQuick one before I ship: is the rollback documented?",
            f"Notes from the {title} sync\n\nSummarizing the decisions from today.",
            "Weekly update\n\nOn track for the milestone; fuller writeup to follow.",
            f"Heads up: {slug} incident\n\nSeeing 5xx on the hot path — digging in now.",
        ]

    if provider == "calendar":
        # calendar inject creates an event → text is the title/summary
        return [
            f"{title} sync",
            "Design review",
            f"{team or 'Team'} weekly",
            "1:1",
            "Roadmap check-in",
            f"Interview — {slug} candidate",
        ]

    if provider == "notion":
        # notion inject creates a page → text is the page title
        return [
            f"Design doc: {slug}",
            f"{title} runbook",
            f"RFC: {slug} rollout",
            "Weekly sync notes",
            f"{slug} postmortem",
            f"Spec: {title} v2",
        ]

    if provider == "drive":
        # drive inject creates a file → text is the file name
        return [
            f"{title} design doc",
            f"{slug} runbook",
            "Q-planning",
            "Roadmap",
            f"{slug} architecture",
            f"Incident review: {slug}",
        ]

    if provider == "jira":
        # jira inject creates an issue → text is the summary
        return [
            f"Fix flaky retry in {slug}",
            f"Investigate {slug} timeout",
            f"Add pagination to {slug}",
            f"{slug} crashes on empty payload",
            f"Improve {slug} error messages",
            f"Tighten {slug} validation",
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


_GH_PEM_PATH = "/tmp/github-app.pem"


async def credentials(pool: asyncpg.Pool, run_id: UUID) -> dict:
    """Everything Fyralis needs to point at this run. Also writes the GitHub
    private key to a .pem so it can be referenced by path."""
    creds: dict = {"base_urls": {
        "slack": "http://localhost:7001/api/",
        "github": "http://localhost:7003",
        "discord_rest": "http://localhost:7002/api/v10",
        "discord_gateway": "ws://localhost:7002/gateway",
        "gmail": "http://localhost:7004/gmail/v1",
        "gmail_token": "http://localhost:7004/token",
        "gmail_jwks": "http://localhost:7004/jwks",
        "gmail_directory": "http://localhost:7004/admin/directory/v1",
        "calendar": "http://localhost:7005/calendar/v3",
        "calendar_token": "http://localhost:7005/token",
        "notion": "http://localhost:7006",
        "drive": "http://localhost:7007/drive/v3",
        "drive_token": "http://localhost:7007/token",
        "jira": "http://localhost:7008",
    }}

    ws = await pool.fetchrow(
        "SELECT bot_token, team_id, team_name, signing_secret FROM app_slack.workspaces WHERE run_id=$1", run_id)
    if ws:
        creds["slack"] = {
            "bot_token": ws["bot_token"], "team_id": ws["team_id"], "team_name": ws["team_name"],
            "signing_secret": ws["signing_secret"],
            "secret_store_key": f"slack_bot_token:{ws['team_id']}",
        }

    app = await pool.fetchrow(
        "SELECT id, app_id, private_key, webhook_secret FROM app_github.apps WHERE run_id=$1", run_id)
    if app:
        inst = await pool.fetchrow(
            "SELECT installation_id FROM app_github.installations WHERE app_pk=$1", app["id"])
        pem_path: Optional[str] = _GH_PEM_PATH
        try:
            pk = app["private_key"]
            with open(_GH_PEM_PATH, "w") as f:
                f.write(pk if pk.endswith("\n") else pk + "\n")
        except OSError:
            pem_path = None
        creds["github"] = {
            "app_id": app["app_id"],
            "installation_id": inst["installation_id"] if inst else None,
            "webhook_secret": app["webhook_secret"],
            "private_key_path": pem_path,
            "private_key": app["private_key"],
        }

    dapp = await pool.fetchrow(
        "SELECT id, application_id, bot_token, public_key FROM app_discord.applications WHERE run_id=$1", run_id)
    if dapp:
        g = await pool.fetchrow("SELECT guild_id FROM app_discord.guilds WHERE application_pk=$1", dapp["id"])
        creds["discord"] = {
            "bot_token": dapp["bot_token"], "application_id": dapp["application_id"],
            "guild_id": g["guild_id"] if g else None, "public_key": dapp["public_key"],
        }

    # Gmail (DWD): the mock doesn't verify the SA signature, so any fake SA JSON
    # works as long as its token_uri points at gmail_token. For the Pub/Sub push,
    # point GOOGLE_OIDC_JWKS_URL at gmail_jwks and use the audience + push SA below.
    cust = await pool.fetchrow(
        "SELECT customer_id, domain, service_account_email, pubsub_audience FROM app_gmail.customers WHERE run_id=$1",
        run_id)
    if cust:
        creds["gmail"] = {
            "customer_id": cust["customer_id"], "domain": cust["domain"],
            "service_account_email": cust["service_account_email"],
            "push_audience": cust["pubsub_audience"],
            "push_sa_email": cust["service_account_email"],
        }
        creds["calendar"] = {
            "customer_id": cust["customer_id"], "domain": cust["domain"],
            "service_account_email": cust["service_account_email"],
        }

    notion = await pool.fetchrow(
        "SELECT bot_token, workspace_id, workspace_name, verification_token FROM app_notion.integrations WHERE run_id=$1",
        run_id)
    if notion:
        creds["notion"] = {
            "bot_token": notion["bot_token"], "workspace_id": notion["workspace_id"],
            "workspace_name": notion["workspace_name"],
            "webhook_verification_token": notion["verification_token"],
        }

    # Google Drive (DWD): same posture as Gmail/Calendar — any fake SA JSON whose
    # token_uri points at drive_token works; the mock doesn't verify the SA sig.
    drive = await pool.fetchrow(
        "SELECT customer_id, domain, service_account_email FROM app_drive.installations WHERE run_id=$1",
        run_id)
    if drive:
        creds["drive"] = {
            "customer_id": drive["customer_id"], "domain": drive["domain"],
            "service_account_email": drive["service_account_email"],
        }

    jira = await pool.fetchrow(
        "SELECT base_url, site_name, cloud_id, account_email, account_id, api_token, webhook_secret "
        "FROM app_jira.installations WHERE run_id=$1", run_id)
    if jira:
        creds["jira"] = {
            "base_url": jira["base_url"], "site_name": jira["site_name"],
            "cloud_id": jira["cloud_id"], "account_email": jira["account_email"],
            "account_id": jira["account_id"], "api_token": jira["api_token"],
            "webhook_secret": jira["webhook_secret"],
        }
    return creds


async def inject_github(pool: asyncpg.Pool, run_id: UUID, *, handle: str, repo: str, text: str) -> dict:
    """A GitHub 'message' = a newly opened issue (title=text), by the actor."""
    repos = await list_repos(pool, run_id)
    if repo not in repos:
        if not repos:
            raise ValueError("no github repos for this run")
        repo = repos[0]
    event_id = await inject_github_event(pool, run_id, kind="issues", repo=repo, handle=handle, title=text)
    return {"provider": "github", "repo": repo, "event_id": str(event_id)}


# --------------------------------------------------------------------------- new-provider targets

async def list_notion_databases(pool: asyncpg.Pool, run_id: UUID) -> list[str]:
    integ = await pool.fetchrow("SELECT id FROM app_notion.integrations WHERE run_id=$1", run_id)
    if not integ:
        return []
    rows = await pool.fetch(
        "SELECT title FROM app_notion.databases WHERE integration_pk=$1 ORDER BY title", integ["id"])
    return [r["title"] for r in rows]


# --------------------------------------------------------------------------- inject: gmail / calendar / notion

async def _person(pool, run_id, handle) -> Optional[dict]:
    return await pool.fetchrow(
        "SELECT id, handle, full_name, email FROM org.people WHERE run_id=$1 AND handle=$2",
        run_id, handle)


def _notion_rt(c: str) -> list:
    return [{"type": "text", "text": {"content": c, "link": None},
             "annotations": {"bold": False, "italic": False, "strikethrough": False,
                             "underline": False, "code": False, "color": "default"},
             "plain_text": c, "href": None}]


async def inject_gmail(pool: asyncpg.Pool, run_id: UUID, *, handle: str, recipient: str, text: str) -> dict:
    """Send a live email from ``handle`` to ``recipient`` — lands in the sender's
    SENT and the recipient's INBOX, both immediately visible over the REST API."""
    from email.utils import format_datetime, make_msgid
    cust = await pool.fetchrow("SELECT id, domain FROM app_gmail.customers WHERE run_id=$1", run_id)
    if not cust:
        raise ValueError("no gmail customer for this run")
    sender = await _person(pool, run_id, handle)
    if not sender:
        raise ValueError(f"unknown person: {handle}")
    recp = await _person(pool, run_id, recipient)
    if not recp or recp["id"] == sender["id"]:
        recp = await pool.fetchrow(
            "SELECT id, handle, full_name, email FROM org.people WHERE run_id=$1 AND id<>$2 LIMIT 1",
            run_id, sender["id"])
    if not recp:
        raise ValueError("need at least two people to send mail")
    now = datetime.now(timezone.utc)
    rfc_id = make_msgid(domain=cust["domain"])
    subject = text.strip().splitlines()[0][:78] if text.strip() else "(no subject)"
    headers = [
        {"name": "From", "value": f"{sender['full_name']} <{sender['email']}>"},
        {"name": "To", "value": recp["email"]},
        {"name": "Subject", "value": subject},
        {"name": "Date", "value": format_datetime(now)},
        {"name": "Message-ID", "value": rfc_id},
    ]
    out = {"provider": "gmail", "from": sender["email"], "to": recp["email"], "subject": subject}
    for email_addr, labels, is_sender in ((sender["email"], ["SENT"], True),
                                          (recp["email"], ["INBOX", "UNREAD"], False)):
        mbox = await pool.fetchrow(
            "SELECT id, history_id FROM app_gmail.mailboxes WHERE customer_pk=$1 AND email=$2",
            cust["id"], email_addr)
        if not mbox:
            continue
        new_hid = int(mbox["history_id"]) + 1
        tpk, gtid, gmid = uuid4(), gmail_thread_id(), gmail_message_id()
        await pool.execute(
            "INSERT INTO app_gmail.threads (id, mailbox_pk, thread_id, subject, snippet) VALUES ($1,$2,$3,$4,$5)",
            tpk, mbox["id"], gtid, subject, text[:100])
        await pool.execute(
            """INSERT INTO app_gmail.messages
                (id, thread_pk, message_id, history_id, rfc822_msg_id, label_ids, headers,
                 snippet, body_plain, body_html, internal_date, size_estimate)
               VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8,$9,'',$10,$11)""",
            uuid4(), tpk, gmid, new_hid, rfc_id, json.dumps(labels), json.dumps(headers),
            text[:120].replace("\n", " "), text, now, len(text) + 200)
        await pool.execute(
            """INSERT INTO app_gmail.history (mailbox_pk, history_id, history_type, message_id, thread_id, label_ids, occurred_at)
               VALUES ($1,$2,'messageAdded',$3,$4,$5::jsonb,$6)""",
            mbox["id"], new_hid, gmid, gtid, json.dumps(labels), now)
        await pool.execute("UPDATE app_gmail.mailboxes SET history_id=$1 WHERE id=$2", new_hid, mbox["id"])
        if is_sender:
            out["message_id"] = gmid
    return out


async def inject_calendar(pool: asyncpg.Pool, run_id: UUID, *, handle: str, attendee: str, text: str) -> dict:
    """Create a live calendar event on ``handle``'s calendar (summary=text),
    optionally inviting ``attendee`` — visible immediately + via incremental sync."""
    organizer = await _person(pool, run_id, handle)
    if not organizer:
        raise ValueError(f"unknown person: {handle}")
    cal = await pool.fetchrow(
        """SELECT c.id, c.calendar_id FROM app_calendar.calendars c
             JOIN app_calendar.accounts a ON a.id=c.account_pk
            WHERE a.run_id=$1 AND c.calendar_id=$2""", run_id, organizer["email"])
    if not cal:
        raise ValueError("no calendar for this person")
    now = datetime.now(timezone.utc)
    end = now + timedelta(minutes=30)
    attendees = [{"email": organizer["email"], "displayName": organizer["full_name"],
                  "organizer": True, "self": True, "responseStatus": "accepted"}]
    att = await _person(pool, run_id, attendee)
    if att and att["id"] != organizer["id"]:
        attendees.append({"email": att["email"], "displayName": att["full_name"],
                          "responseStatus": "needsAction"})
    event_id = gcal_event_id()
    await pool.execute(
        """INSERT INTO app_calendar.events
            (id, calendar_pk, event_id, status, summary, description, location,
             start_time, end_time, all_day, organizer_email, creator_email, attendees,
             recurring_event_id, event_type, hangout_link, html_link, sequence, ical_uid,
             created_at, updated_at)
           VALUES ($1,$2,$3,'confirmed',$4,'','',$5,$6,FALSE,$7,$7,$8::jsonb,
                   NULL,'default',NULL,$9,0,$10,$11,$11)""",
        uuid4(), cal["id"], event_id, (text[:200] or "(untitled)"), now, end, organizer["email"],
        json.dumps(attendees), f"https://www.google.com/calendar/event?eid={event_id}",
        gcal_ical_uid(), now)
    return {"provider": "calendar", "calendar": cal["calendar_id"], "event_id": event_id, "summary": text[:200]}


async def inject_notion(pool: asyncpg.Pool, run_id: UUID, *, handle: str, database: str, text: str) -> dict:
    """Create a live Notion page (title=text) in ``database`` — visible via search,
    database query, and page hydration immediately."""
    integ = await pool.fetchrow("SELECT id FROM app_notion.integrations WHERE run_id=$1", run_id)
    if not integ:
        raise ValueError("no notion integration for this run")
    db = await pool.fetchrow(
        "SELECT id, database_id FROM app_notion.databases WHERE integration_pk=$1 AND title=$2",
        integ["id"], database)
    if not db:
        db = await pool.fetchrow(
            "SELECT id, database_id FROM app_notion.databases WHERE integration_pk=$1 ORDER BY title LIMIT 1",
            integ["id"])
    if not db:
        raise ValueError("no notion database for this run")
    user_id = notion_id()
    now = datetime.now(timezone.utc)
    page_id = notion_id()
    title = text.strip()[:120] or "Untitled"
    props = {"Name": {"id": "title", "type": "title", "title": _notion_rt(title)},
             "Status": {"id": "statU", "type": "select", "select": {"name": "Draft", "color": "default"}}}
    page_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_notion.pages
            (id, integration_pk, page_id, parent_type, parent_id, database_pk, title, properties,
             icon, archived, url, created_by, created_time, last_edited_time)
           VALUES ($1,$2,$3,'database_id',$4,$5,$6,$7::jsonb,NULL,FALSE,$8,$9,$10,$10)""",
        page_pk, integ["id"], page_id, db["database_id"], db["id"], title, json.dumps(props),
        f"https://www.notion.so/{page_id.replace('-', '')}", user_id, now)
    await pool.execute(
        """INSERT INTO app_notion.blocks
            (id, page_pk, block_id, parent_block_id, type, content, has_children, position,
             created_by, created_time, last_edited_time)
           VALUES ($1,$2,$3,NULL,'paragraph',$4::jsonb,FALSE,0,$5,$6,$6)""",
        uuid4(), page_pk, notion_id(),
        json.dumps({"rich_text": _notion_rt("Created via Spammer Studio."), "color": "default"}),
        user_id, now)
    return {"provider": "notion", "database": database, "page_id": page_id, "title": title}


async def list_jira_projects(pool: asyncpg.Pool, run_id: UUID) -> list[str]:
    inst = await pool.fetchrow("SELECT id FROM app_jira.installations WHERE run_id=$1", run_id)
    if not inst:
        return []
    rows = await pool.fetch(
        "SELECT key FROM app_jira.projects WHERE installation_pk=$1 ORDER BY key", inst["id"])
    return [r["key"] for r in rows]


async def inject_drive(pool: asyncpg.Pool, run_id: UUID, *, handle: str, title: str) -> dict:
    """Create a live Drive file (name=text) on ``handle``'s My Drive — visible via
    files.list + the changes feed immediately; drives the changes-poll live path."""
    eid = await _inject_drive_file(pool, run_id, handle=handle, title=title)
    return {"provider": "drive", "file": (title or "")[:120], "event_id": str(eid)}


async def inject_jira(pool: asyncpg.Pool, run_id: UUID, *, handle: str, project: str, summary: str) -> dict:
    """Create a live Jira issue (summary=text) in ``project`` (or the first
    project) with a status transition — visible via search/jql + drives the
    signed webhook live path."""
    eid = await _inject_jira_issue(pool, run_id, handle=handle, project=project or None, summary=summary)
    return {"provider": "jira", "summary": (summary or "")[:200], "event_id": str(eid)}
