"""End-to-end OrgGen compiler.

Inputs:  org.runs row + the linked profile spec
Outputs: populates org.teams, org.people, org.projects, timeline.events,
         app_slack.{workspaces,channels,users,messages}

Idempotent on the run_id: clears and regenerates.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Sequence
from uuid import UUID, uuid4

import asyncpg
import structlog

from spammers.common.ids import (
    discord_bot_token,
    discord_snowflake,
    gcal_event_id,
    gcal_ical_uid,
    gmail_message_id,
    gmail_thread_id,
    github_app_id,
    github_installation_id,
    github_repo_id,
    github_sha,
    github_user_id,
    github_webhook_secret,
    notion_id,
    notion_token,
    notion_verification_token,
    slack_app_id,
    slack_bot_token,
    slack_channel_id,
    slack_client_id,
    slack_client_secret,
    slack_signing_secret,
    slack_team_id,
    slack_ts,
    slack_user_id,
)
from spammers.common.signing import generate_ed25519_keypair, generate_rsa_keypair
from spammers.orggen.personas import Person, generate_people
from spammers.orggen.profiles import ProfileSpec
from spammers.orggen.projects import Project, generate_projects
from spammers.orggen.seed import RunRandom
from spammers.orggen.timeline import (
    TimelineEvent,
    compile_calendar_events,
    compile_discord_events,
    compile_slack_events,
)


log = structlog.get_logger("spammers.orggen.compile")


async def compile_run(pool: asyncpg.Pool, run_id: UUID) -> dict:
    """Compile the full timeline for a run. Returns a small summary dict."""
    row = await pool.fetchrow(
        "SELECT id, size, runtime, seed, virtual_now, fyralis_tenant_id FROM org.runs WHERE id = $1",
        run_id,
    )
    if row is None:
        raise LookupError(f"run not found: {run_id}")

    from spammers.orggen.profiles import resolve
    spec = resolve(row["size"], row["runtime"])
    virtual_now: datetime = row["virtual_now"]
    seed = int(row["seed"])

    rng = RunRandom(seed)

    log.info("orggen_start", run_id=str(run_id), size=spec.size, runtime=spec.runtime,
             people=spec.people, daily_events=spec.daily_events)

    # 1. People & teams
    people, team_names = generate_people(spec, rng, virtual_now=virtual_now)

    # 2. Projects
    projects = generate_projects(spec, rng, people, virtual_now=virtual_now)

    # 3. Timeline — Slack + Discord message streams.
    slack_events = compile_slack_events(spec, rng, people, projects, virtual_now=virtual_now)
    discord_events = compile_discord_events(spec, rng, people, projects, virtual_now=virtual_now)
    calendar_events = compile_calendar_events(spec, rng, people, projects, virtual_now=virtual_now)

    # 4. Persist everything in one transaction
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _clear_existing(conn, run_id)
            team_ids = await _insert_teams(conn, run_id, team_names)
            await _insert_people(conn, run_id, people, team_ids)
            await _insert_projects(conn, run_id, projects, people)

            # Slack workspace projection
            workspace_id, channel_ids, slack_user_pks = await _create_slack_workspace(
                conn, run_id, spec, rng.sub("slack_setup"), people, projects,
            )

            # Discord application + guild + channels + users
            discord_chan_pks, discord_user_pks = await _create_discord_application(
                conn, run_id, rng.sub("discord_setup"), people, projects,
            )

            # GitHub app + installation + repositories, then projected content.
            github_repos = await _create_github_app(conn, run_id, projects, virtual_now)
            github_counts = await _generate_github_content(
                conn, run_id, github_repos, people, rng.sub("github"), virtual_now, spec,
            )

            # Google Calendar account + one calendar per person.
            calendar_pks = await _create_calendar_account(conn, run_id, rng.sub("calendar_setup"), people)

            # Notion workspace (integration + databases + pages + blocks + comments).
            notion_counts = await _generate_notion_content(
                conn, run_id, projects, people, rng.sub("notion"), virtual_now, spec,
            )

            # Gmail customer + mailboxes + threaded mail + per-mailbox history.
            gmail_counts = await _generate_gmail_content(
                conn, run_id, projects, people, rng.sub("gmail"), virtual_now, spec,
            )

            # Timeline events + message projections (Slack + Discord + Calendar)
            await _insert_timeline_events(conn, run_id, slack_events, virtual_now)
            await _insert_timeline_events(conn, run_id, discord_events, virtual_now)
            await _insert_timeline_events(conn, run_id, calendar_events, virtual_now)
            await _project_slack_messages(
                conn, slack_events, virtual_now, workspace_id, channel_ids, slack_user_pks, people,
            )
            await _project_discord_messages(
                conn, discord_events, virtual_now, discord_chan_pks, discord_user_pks,
            )
            await _project_calendar_events(
                conn, calendar_events, virtual_now, calendar_pks, people,
            )

            await conn.execute(
                "UPDATE org.runs SET finalized_at = now() WHERE id = $1",
                run_id,
            )

    log.info("orggen_done", run_id=str(run_id),
             people=len(people), projects=len(projects),
             slack_events=len(slack_events), discord_events=len(discord_events),
             calendar_events=len(calendar_events),
             github_repos=len(github_repos), github_prs=github_counts["pull_requests"])

    return {
        "people": len(people),
        "teams": len(team_names),
        "projects": len(projects),
        "slack_events": len(slack_events),
        "discord_events": len(discord_events),
        "calendar_events": len(calendar_events),
        "github_repos": len(github_repos),
        **{f"github_{k}": v for k, v in github_counts.items()},
        **{f"notion_{k}": v for k, v in notion_counts.items()},
        **{f"gmail_{k}": v for k, v in gmail_counts.items()},
    }


async def _clear_existing(conn, run_id: UUID) -> None:
    # Cascading deletes from org.runs would also work, but we want to keep
    # the run row.  We delete what we know we generate.
    await conn.execute("DELETE FROM timeline.events WHERE run_id = $1", run_id)
    await conn.execute(
        "DELETE FROM app_slack.workspaces WHERE run_id = $1", run_id,
    )
    await conn.execute("DELETE FROM app_discord.applications WHERE run_id = $1", run_id)
    await conn.execute("DELETE FROM app_github.apps WHERE run_id = $1", run_id)
    await conn.execute("DELETE FROM app_calendar.accounts WHERE run_id = $1", run_id)
    await conn.execute("DELETE FROM app_notion.integrations WHERE run_id = $1", run_id)
    await conn.execute("DELETE FROM app_gmail.customers WHERE run_id = $1", run_id)
    await conn.execute("DELETE FROM org.projects WHERE run_id = $1", run_id)
    await conn.execute("DELETE FROM org.people WHERE run_id = $1", run_id)
    await conn.execute("DELETE FROM org.teams WHERE run_id = $1", run_id)


async def _insert_teams(conn, run_id: UUID, team_names: Sequence[str]) -> dict[str, UUID]:
    ids: dict[str, UUID] = {}
    for name in team_names:
        tid = uuid4()
        ids[name] = tid
        await conn.execute(
            "INSERT INTO org.teams(id, run_id, name) VALUES ($1, $2, $3)",
            tid, run_id, name,
        )
    return ids


async def _insert_people(conn, run_id: UUID, people: Sequence[Person], team_ids: dict[str, UUID]) -> None:
    rows = [
        (
            p.id, run_id, p.handle, p.full_name, p.email, p.role, p.level,
            team_ids.get(p.team_name), p.timezone, p.started_at, p.ended_at,
            json.dumps(p.voice_signature),
        )
        for p in people
    ]
    await conn.executemany(
        """
        INSERT INTO org.people(id, run_id, handle, full_name, email, role, level,
                               team_id, timezone, started_at, ended_at, voice_signature)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb)
        """,
        rows,
    )


async def _insert_projects(conn, run_id: UUID, projects: Sequence[Project], people: Sequence[Person]) -> None:
    handle_to_id = {p.handle: p.id for p in people}
    rows = [
        (
            proj.id, run_id, proj.slug, proj.title, handle_to_id.get(proj.owner_handle),
            proj.started_at, proj.ended_at,
            json.dumps(proj.repos), json.dumps(proj.slack_channels),
            json.dumps(proj.discord_channels), json.dumps(proj.email_thread_anchors),
        )
        for proj in projects
    ]
    await conn.executemany(
        """
        INSERT INTO org.projects(id, run_id, slug, title, owner_id, started_at, ended_at,
                                 repos, slack_channels, discord_channels, email_thread_anchors)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10::jsonb, $11::jsonb)
        """,
        rows,
    )


# ---------------- Slack projection ----------------

async def _create_slack_workspace(
    conn,
    run_id: UUID,
    spec: ProfileSpec,
    rng: RunRandom,
    people: Sequence[Person],
    projects: Sequence[Project],
) -> tuple[UUID, dict[str, UUID], dict[UUID, UUID]]:
    """Create workspace, channels, users. Returns (workspace_id, name→channel_pk, person_id→user_pk)."""
    workspace_id = uuid4()
    team_id = slack_team_id()
    await conn.execute(
        """
        INSERT INTO app_slack.workspaces
            (id, run_id, team_id, team_name, team_domain, signing_secret,
             client_id, client_secret, bot_token, bot_user_id, app_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """,
        workspace_id, run_id, team_id, "Spammer Org", "spammer-org",
        slack_signing_secret(), slack_client_id(), slack_client_secret(),
        slack_bot_token(), slack_user_id(), slack_app_id(),
    )

    # users
    user_pks: dict[UUID, UUID] = {}
    user_id_str: dict[UUID, str] = {}
    for person in people:
        uid = uuid4()
        slack_uid = slack_user_id()
        user_pks[person.id] = uid
        user_id_str[person.id] = slack_uid
        await conn.execute(
            """
            INSERT INTO app_slack.users
                (id, workspace_id, person_id, slack_user_id, is_bot, deleted, profile)
            VALUES ($1, $2, $3, $4, FALSE, FALSE, $5::jsonb)
            """,
            uid, workspace_id, person.id, slack_uid,
            json.dumps({
                "real_name": person.full_name,
                "display_name": person.handle,
                "email": person.email,
                "tz": person.timezone,
                "title": person.role,
            }),
        )

    # channels — #general + #random + one per team + one per project
    channel_names: list[tuple[str, bool, str]] = [
        ("general", True, "Company-wide announcements"),
        ("random", False, "Off-topic"),
        ("help", False, "Ask questions"),
    ]
    teams_seen = set()
    for p in people:
        team_chan = f"{p.team_name.lower()}-standup"
        if team_chan not in teams_seen:
            teams_seen.add(team_chan)
            channel_names.append((team_chan, False, f"Standups for {p.team_name}"))
    for proj in projects:
        for chan in proj.slack_channels:
            name = chan.lstrip("#")
            channel_names.append((name, False, proj.title))

    # Dedup by name (keep first)
    seen = set()
    deduped = []
    for n, isgen, purpose in channel_names:
        if n in seen:
            continue
        seen.add(n)
        deduped.append((n, isgen, purpose))

    chan_pks: dict[str, UUID] = {}
    earliest = next((p.started_at for p in people), datetime.now(timezone.utc))
    for name, is_general, purpose in deduped:
        cid = uuid4()
        chan_pks[name] = cid
        await conn.execute(
            """
            INSERT INTO app_slack.channels
                (id, workspace_id, channel_id, name, is_private, is_general,
                 topic, purpose, created_at)
            VALUES ($1, $2, $3, $4, FALSE, $5, $6, $7, $8)
            """,
            cid, workspace_id, slack_channel_id(), name, is_general,
            "", purpose, earliest,
        )

    return workspace_id, chan_pks, user_pks


async def _insert_timeline_events(conn, run_id: UUID, events: Sequence[TimelineEvent], virtual_now: datetime) -> None:
    rows = [
        (
            e.id, run_id, e.virtual_ts, e.type, e.actor_id, e.project_id,
            json.dumps(e.payload), json.dumps(e.cross_refs),
            e.virtual_ts <= virtual_now,
        )
        for e in events
    ]
    if not rows:
        return
    await conn.executemany(
        """
        INSERT INTO timeline.events
            (id, run_id, virtual_ts, type, actor_id, project_id, payload,
             cross_refs, is_historical)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9)
        """,
        rows,
    )


async def _create_github_app(
    conn, run_id: UUID, projects: Sequence[Project], virtual_now: datetime,
) -> list[dict]:
    """Seed one GitHub App + installation + the repositories from projects.

    Returns the repository records (pk id, owner, name, full_name) for content gen.
    """
    # Unique repos across projects (project.repos holds "owner/name" strings).
    seen: dict[str, tuple[str, str]] = {}
    for proj in projects:
        for full in proj.repos:
            if "/" in full and full not in seen:
                owner, name = full.split("/", 1)
                seen[full] = (owner, name)
    if not seen:
        seen["acme/core"] = ("acme", "core")

    account_login = next(iter(seen.values()))[0]
    app_id = github_app_id()
    private_pem, public_pem = generate_rsa_keypair()
    app_pk = uuid4()
    await conn.execute(
        """
        INSERT INTO app_github.apps
            (id, run_id, app_id, slug, name, client_id, client_secret, webhook_secret,
             private_key, public_key, permissions, events)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12::jsonb)
        """,
        app_pk, run_id, app_id, f"{account_login}-ingest", f"{account_login.title()} Ingest",
        "Iv1." + secrets.token_hex(8), secrets.token_hex(20), github_webhook_secret(),
        private_pem, public_pem,
        json.dumps({"contents": "read", "metadata": "read", "issues": "read",
                    "pull_requests": "read", "checks": "read"}),
        json.dumps(["push", "pull_request", "issues", "issue_comment",
                    "pull_request_review", "check_run"]),
    )

    installation_pk = uuid4()
    await conn.execute(
        """
        INSERT INTO app_github.installations
            (id, app_pk, installation_id, account_login, account_type, account_id,
             repository_selection, created_at)
        VALUES ($1, $2, $3, $4, 'Organization', $5, 'all', $6)
        """,
        installation_pk, app_pk, github_installation_id(), account_login,
        github_user_id(), virtual_now,
    )

    records: list[dict] = []
    rows = []
    for owner, name in seen.values():
        repo_pk = uuid4()
        rows.append((repo_pk, installation_pk, github_repo_id(), owner, name, False, "main",
                     f"The {name} service.", virtual_now))
        records.append({"id": repo_pk, "owner": owner, "name": name, "full_name": f"{owner}/{name}"})
    await conn.executemany(
        """
        INSERT INTO app_github.repositories
            (id, installation_pk, repo_id, owner, name, private, default_branch,
             description, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
        rows,
    )
    return records


_PR_TITLES = [
    "Fix flaky retry in {svc}", "Add pagination to {svc} list endpoint",
    "Bump dependencies for {svc}", "Refactor {svc} config loading",
    "Handle 429s in {svc} client", "Improve {svc} error messages",
    "Cache {svc} lookups", "Tighten {svc} input validation",
]
_ISSUE_TITLES = [
    "{svc}: intermittent timeouts under load", "{svc} returns stale data after deploy",
    "Document the {svc} setup steps", "{svc} logs are too noisy",
    "Add metrics for {svc}", "{svc} crashes on empty payload",
]
_COMMIT_MSGS = [
    "fix: guard against nil response", "chore: bump deps", "refactor: extract helper",
    "feat: add retry budget", "test: cover edge case", "docs: update README",
    "perf: avoid extra allocation", "fix: off-by-one in cursor",
]
_REVIEW_STATES = ["approved", "approved", "commented", "changes_requested"]
_CHECK_NAMES = ["build", "test", "lint"]
_CHECK_CONCLUSIONS = ["success", "success", "success", "failure", "neutral"]


async def _generate_github_content(
    conn, run_id: UUID, repos: Sequence[dict], people: Sequence[Person],
    rng: RunRandom, virtual_now: datetime, spec: ProfileSpec,
) -> dict:
    """Generate PRs / issues / commits / reviews / comments / check-runs per repo,
    projecting them into app_github.* and emitting a timeline.events row for each."""
    plist = list(people)
    earliest = virtual_now - spec.duration
    span = max(1.0, (virtual_now - earliest).total_seconds())
    counts = {"pull_requests": 0, "issues": 0, "commits": 0,
              "reviews": 0, "issue_comments": 0, "check_runs": 0}

    def when() -> datetime:
        return earliest + timedelta(seconds=rng.uniform(0, span))

    async def event(virtual_ts: datetime, etype: str, actor_id, payload: dict) -> UUID:
        eid = uuid4()
        await conn.execute(
            """
            INSERT INTO timeline.events (id, run_id, virtual_ts, type, actor_id, payload, is_historical)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
            """,
            eid, run_id, virtual_ts, etype, actor_id, json.dumps(payload), virtual_ts <= virtual_now,
        )
        return eid

    for repo in repos:
        repo_pk, full = repo["id"], repo["full_name"]
        number = 0

        # Commits
        shas: list[str] = []
        prev: list[str] = []
        for _ in range(rng.randint(4, 9)):
            author = rng.choice(plist)
            sha = github_sha()
            committed = when()
            adds, dels = rng.randint(1, 200), rng.randint(0, 80)
            await conn.execute(
                """
                INSERT INTO app_github.commits
                    (id, repo_pk, sha, message, author_login, author_email, committed_at,
                     parents, additions, deletions)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10)
                """,
                uuid4(), repo_pk, sha, rng.choice(_COMMIT_MSGS), author.handle, author.email,
                committed, json.dumps(prev[-1:]), adds, dels,
            )
            shas.append(sha)
            prev = [sha]
            counts["commits"] += 1

        # Pull requests (+ reviews + check-runs)
        for _ in range(rng.randint(2, 5)):
            number += 1
            author = rng.choice(plist)
            created = when()
            closed = merged = None
            state = "open"
            if rng.bool_with_prob(0.6):
                state = "closed"
                closed = created + timedelta(hours=rng.randint(2, 240))
                if closed > virtual_now:
                    closed = virtual_now
                if rng.bool_with_prob(0.7):
                    merged = closed
            head_sha = rng.choice(shas) if shas else github_sha()
            base_sha = rng.choice(shas) if shas else github_sha()
            svc = repo["name"]
            pr_pk = uuid4()
            ev = await event(created, "github.pull_request", author.id,
                             {"action": "opened", "repo": full, "number": number})
            await conn.execute(
                """
                INSERT INTO app_github.pull_requests
                    (id, repo_pk, number, title, body, state, merged, user_login, head_ref,
                     head_sha, base_ref, base_sha, additions, deletions, changed_files, labels,
                     created_at, updated_at, merged_at, closed_at, timeline_event_id)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'main',$11,$12,$13,$14,$15::jsonb,$16,$17,$18,$19,$20)
                """,
                pr_pk, repo_pk, number, _PR_TITLES[number % len(_PR_TITLES)].format(svc=svc),
                f"This PR updates {svc}.", state, merged is not None, author.handle,
                f"feature/{svc}-{number}", head_sha, base_sha,
                rng.randint(1, 300), rng.randint(0, 120), rng.randint(1, 12),
                json.dumps(rng.sample(["bug", "enhancement", "chore", "deps"], k=rng.randint(0, 2))),
                created, closed or created, merged, closed, ev,
            )
            counts["pull_requests"] += 1

            for _ in range(rng.randint(0, 2)):
                reviewer = rng.choice(plist)
                rstate = rng.choice(_REVIEW_STATES)
                submitted = created + timedelta(hours=rng.randint(1, 48))
                rev_ev = await event(submitted, "github.pull_request_review", reviewer.id,
                                     {"action": "submitted", "repo": full, "number": number, "state": rstate})
                await conn.execute(
                    """
                    INSERT INTO app_github.reviews (id, pr_pk, user_login, state, body, submitted_at, timeline_event_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    uuid4(), pr_pk, reviewer.handle, rstate, "LGTM" if rstate == "approved" else "Some comments.",
                    submitted, rev_ev,
                )
                counts["reviews"] += 1

            for _ in range(rng.randint(1, 2)):
                concl = rng.choice(_CHECK_CONCLUSIONS)
                started = created + timedelta(minutes=rng.randint(1, 30))
                cr_ev = await event(started, "github.check_run", author.id,
                                    {"action": "completed", "repo": full, "head_sha": head_sha, "conclusion": concl})
                await conn.execute(
                    """
                    INSERT INTO app_github.check_runs
                        (id, repo_pk, name, head_sha, status, conclusion, started_at, completed_at, timeline_event_id)
                    VALUES ($1, $2, $3, $4, 'completed', $5, $6, $7, $8)
                    """,
                    uuid4(), repo_pk, rng.choice(_CHECK_NAMES), head_sha, concl,
                    started, started + timedelta(minutes=rng.randint(1, 15)), cr_ev,
                )
                counts["check_runs"] += 1

        # Issues (+ comments)
        for _ in range(rng.randint(1, 4)):
            number += 1
            author = rng.choice(plist)
            created = when()
            state = "open"
            closed = None
            if rng.bool_with_prob(0.5):
                state = "closed"
                closed = min(virtual_now, created + timedelta(hours=rng.randint(2, 300)))
            svc = repo["name"]
            ev = await event(created, "github.issues", author.id,
                             {"action": "opened", "repo": full, "number": number})
            await conn.execute(
                """
                INSERT INTO app_github.issues
                    (id, repo_pk, number, title, body, state, user_login, assignees, labels,
                     created_at, updated_at, closed_at, timeline_event_id)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10,$11,$12,$13)
                """,
                uuid4(), repo_pk, number, _ISSUE_TITLES[number % len(_ISSUE_TITLES)].format(svc=svc),
                f"Observed in {svc}.", state, author.handle,
                json.dumps([]), json.dumps(rng.sample(["bug", "question", "wontfix"], k=rng.randint(0, 1))),
                created, closed or created, closed, ev,
            )
            counts["issues"] += 1

            for _ in range(rng.randint(0, 3)):
                commenter = rng.choice(plist)
                cwhen = created + timedelta(hours=rng.randint(1, 72))
                c_ev = await event(cwhen, "github.issue_comment", commenter.id,
                                   {"action": "created", "repo": full, "issue_number": number})
                await conn.execute(
                    """
                    INSERT INTO app_github.issue_comments
                        (id, repo_pk, issue_number, user_login, body, created_at, timeline_event_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    uuid4(), repo_pk, number, commenter.handle, "Thanks for the report.", cwhen, c_ev,
                )
                counts["issue_comments"] += 1

    return counts


def _bump_ts(ts: str) -> str:
    """Return the next microsecond after a Slack ``secs.micros`` timestamp."""
    secs, micros = ts.split(".")
    total = int(secs) * 1_000_000 + int(micros) + 1
    return f"{total // 1_000_000}.{total % 1_000_000:06d}"


async def _project_slack_messages(
    conn,
    events: Sequence[TimelineEvent],
    virtual_now: datetime,
    workspace_id: UUID,
    channel_pks: dict[str, UUID],
    user_pks: dict[UUID, UUID],
    people: Sequence[Person],
) -> None:
    """Project ``slack.message`` events into the app_slack.messages table.

    Only historical events (virtual_ts ≤ virtual_now) are projected here. Live
    events get projected at emission time by the slack mock's events.py.
    """
    rows = []
    used_ts: dict[UUID, set[str]] = {}
    for e in events:
        if e.type != "slack.message":
            continue
        if e.virtual_ts > virtual_now:
            continue
        chan_name = e.payload["channel"].lstrip("#")
        chan_pk = channel_pks.get(chan_name)
        if chan_pk is None:
            continue
        user_pk = user_pks.get(e.actor_id)
        # ts must be unique per channel (Slack ts is the per-channel message id).
        # slack_ts has only microsecond precision, so two events in the same
        # channel at the same instant collide — bump by 1µs until unique.
        ts = slack_ts(e.virtual_ts)
        seen = used_ts.setdefault(chan_pk, set())
        while ts in seen:
            ts = _bump_ts(ts)
        seen.add(ts)
        rows.append((
            uuid4(), chan_pk, user_pk, ts, None, None,
            e.payload["text"], None, None, 0, json.dumps([]), None, False,
            e.id,
        ))

    if not rows:
        return
    await conn.executemany(
        """
        INSERT INTO app_slack.messages
            (id, channel_pk, user_pk, ts, thread_ts, subtype, text, blocks,
             attachments, reply_count, reactions, edited, is_hidden, timeline_event_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12, $13, $14)
        """,
        rows,
    )


# ---------------- Discord projection ----------------

async def _create_discord_application(
    conn,
    run_id: UUID,
    rng: RunRandom,
    people: Sequence[Person],
    projects: Sequence[Project],
) -> tuple[dict[str, UUID], dict[UUID, UUID]]:
    """Create the Discord application, guild, users, channels.

    Returns (channel name→channel_pk, person_id→user_pk).
    """
    application_pk = uuid4()
    application_id = discord_snowflake()
    private_hex, public_hex = generate_ed25519_keypair()
    await conn.execute(
        """
        INSERT INTO app_discord.applications
            (id, run_id, application_id, client_id, client_secret, bot_token,
             public_key, private_key)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        application_pk, run_id, application_id, application_id,
        secrets.token_hex(16), discord_bot_token(), public_hex, private_hex,
    )

    earliest = next((p.started_at for p in people), datetime.now(timezone.utc))

    # Users — one per person (1:1 with org.people).
    user_pks: dict[UUID, UUID] = {}
    owner_discord_id: str | None = None
    for person in people:
        uid = uuid4()
        discord_uid = discord_snowflake()
        user_pks[person.id] = uid
        if owner_discord_id is None:
            owner_discord_id = discord_uid
        await conn.execute(
            """
            INSERT INTO app_discord.users
                (id, application_pk, person_id, discord_user_id, username,
                 discriminator, is_bot)
            VALUES ($1, $2, $3, $4, $5, '0', FALSE)
            """,
            uid, application_pk, person.id, discord_uid, person.handle,
        )

    # Guild
    guild_pk = uuid4()
    guild_id = discord_snowflake()
    await conn.execute(
        """
        INSERT INTO app_discord.guilds
            (id, application_pk, guild_id, name, owner_user_id, created_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        guild_pk, application_pk, guild_id, "Spammer Org", owner_discord_id, earliest,
    )

    # Channels — #general + #off-topic + #dev + one per project channel.
    channel_specs: list[tuple[str, str]] = [
        ("general", "General discussion"),
        ("off-topic", "Off-topic chatter"),
        ("dev", "Engineering questions"),
    ]
    for proj in projects:
        for chan in proj.discord_channels:
            channel_specs.append((chan.lstrip("#"), proj.title))

    seen: set[str] = set()
    chan_pks: dict[str, UUID] = {}
    for name, topic in channel_specs:
        if name in seen:
            continue
        seen.add(name)
        cid = uuid4()
        chan_pks[name] = cid
        await conn.execute(
            """
            INSERT INTO app_discord.channels
                (id, guild_pk, channel_id, name, type, topic, created_at)
            VALUES ($1, $2, $3, $4, 0, $5, $6)
            """,
            cid, guild_pk, discord_snowflake(), name, topic, earliest,
        )

    return chan_pks, user_pks


async def _project_discord_messages(
    conn,
    events: Sequence[TimelineEvent],
    virtual_now: datetime,
    channel_pks: dict[str, UUID],
    user_pks: dict[UUID, UUID],
) -> None:
    """Project historical ``discord.message`` events into app_discord.messages.

    Live events (virtual_ts > virtual_now) are dispatched + projected later by
    the mock's GatewayDispatcher. ``message_id`` is a snowflake derived from the
    event's virtual_ts, made unique per channel (snowflakes are per-channel keys).
    """
    rows = []
    used_ids: dict[UUID, set[str]] = {}
    for e in events:
        if e.type != "discord.message" or e.virtual_ts > virtual_now:
            continue
        chan_name = (e.payload.get("channel") or "").lstrip("#")
        chan_pk = channel_pks.get(chan_name)
        if chan_pk is None:
            continue
        user_pk = user_pks.get(e.actor_id)
        message_id = discord_snowflake(e.virtual_ts)
        seen = used_ids.setdefault(chan_pk, set())
        while message_id in seen:
            message_id = str(int(message_id) + 1)
        seen.add(message_id)
        rows.append((
            uuid4(), chan_pk, message_id, user_pk, e.payload.get("text", "") or "",
            0, e.virtual_ts, e.id,
        ))

    if not rows:
        return
    await conn.executemany(
        """
        INSERT INTO app_discord.messages
            (id, channel_pk, message_id, author_user_pk, content, type,
             created_at, timeline_event_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        rows,
    )


# =============================================================================
# Google Calendar
# =============================================================================

async def _create_calendar_account(
    conn, run_id: UUID, rng: RunRandom, people: Sequence[Person],
) -> dict[UUID, UUID]:
    """Seed the Workspace account + service account + one calendar per person.

    Returns ``{person_id: calendar_pk}``. Each person's ``calendar_id`` is their
    email (their primary calendar), matching how the consumer resolves calendars.
    """
    domain = people[0].email.split("@", 1)[1] if people else "example.com"
    private_pem, public_pem = generate_rsa_keypair()
    sa_id = secrets.token_hex(10)
    account_pk = uuid4()
    await conn.execute(
        """
        INSERT INTO app_calendar.accounts
            (id, run_id, customer_id, domain, service_account_email,
             service_account_client_id, service_account_private_key, service_account_public_key)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        account_pk, run_id, "C" + secrets.token_hex(4), domain,
        f"ingest@{domain.split('.')[0]}-ingest.iam.gserviceaccount.com",
        sa_id, private_pem, public_pem,
    )

    calendar_pks: dict[UUID, UUID] = {}
    rows = []
    for person in people:
        cal_pk = uuid4()
        calendar_pks[person.id] = cal_pk
        rows.append((cal_pk, account_pk, person.id, person.email,
                     f"{person.full_name}", person.timezone))
    await conn.executemany(
        """
        INSERT INTO app_calendar.calendars
            (id, account_pk, person_id, calendar_id, summary, time_zone)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        rows,
    )
    return calendar_pks


_RESPONSE_STATUSES = ["accepted", "accepted", "tentative", "needsAction", "declined"]


async def _project_calendar_events(
    conn,
    events: Sequence[TimelineEvent],
    virtual_now: datetime,
    calendar_pks: dict[UUID, UUID],
    people: Sequence[Person],
) -> None:
    """Project ``calendar.event`` events into app_calendar.events.

    Each meeting lands on the organizer's calendar. Attendees are rendered from
    the payload's person-id list into Google's ``attendees[]`` shape.
    """
    by_id: dict[str, Person] = {str(p.id): p for p in people}
    rows = []
    used_ids: dict[UUID, set[str]] = {}
    for e in events:
        if e.type != "calendar.event" or e.virtual_ts > virtual_now:
            continue
        cal_pk = calendar_pks.get(e.actor_id)
        if cal_pk is None:
            continue
        organizer = by_id.get(str(e.actor_id))
        if organizer is None:
            continue

        event_id = gcal_event_id()
        seen = used_ids.setdefault(cal_pk, set())
        while event_id in seen:
            event_id = gcal_event_id()
        seen.add(event_id)

        start = e.virtual_ts
        end = start + timedelta(minutes=int(e.payload.get("duration_mins", 60)))
        attendees = []
        for i, pid in enumerate(e.payload.get("attendee_ids", [])):
            p = by_id.get(pid)
            if p is None:
                continue
            att = {"email": p.email, "displayName": p.full_name}
            if p.id == organizer.id:
                att["organizer"] = True
                att["self"] = True
                att["responseStatus"] = "accepted"
            else:
                att["responseStatus"] = _RESPONSE_STATUSES[(len(event_id) + i) % len(_RESPONSE_STATUSES)]
            attendees.append(att)

        location = e.payload.get("location", "") or ""
        hangout = f"https://meet.google.com/{gcal_event_id()[:3]}-{gcal_event_id()[:4]}-{gcal_event_id()[:3]}" if location == "Meet" else None
        html_link = f"https://www.google.com/calendar/event?eid={event_id}"
        rows.append((
            uuid4(), cal_pk, event_id, "confirmed",
            e.payload.get("summary", ""), "", location,
            start, end, False,
            organizer.email, organizer.email, json.dumps(attendees),
            None, "default", hangout, html_link, 0, gcal_ical_uid(),
            start, start, e.id,
        ))

    if not rows:
        return
    await conn.executemany(
        """
        INSERT INTO app_calendar.events
            (id, calendar_pk, event_id, status, summary, description, location,
             start_time, end_time, all_day, organizer_email, creator_email,
             attendees, recurring_event_id, event_type, hangout_link, html_link,
             sequence, ical_uid, created_at, updated_at, timeline_event_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb,
                $14, $15, $16, $17, $18, $19, $20, $21, $22)
        """,
        rows,
    )


# =============================================================================
# Notion
# =============================================================================

_NOTION_DOC_TITLES = [
    "Design doc: {svc}", "{svc} runbook", "RFC: {svc} rollout", "{svc} postmortem",
    "Onboarding notes", "Weekly sync notes", "{svc} architecture", "Q-planning",
    "Incident review: {svc}", "{svc} API notes", "Roadmap", "Spec: {svc} v2",
]
_NOTION_STATUSES = ["Draft", "In review", "Published", "Archived"]
_NOTION_BLOCK_KINDS = ["paragraph", "paragraph", "heading_2", "bulleted_list_item", "to_do"]
_NOTION_PARAS = [
    "Captured the decisions from today's discussion here.",
    "Open question: how do we want to handle the retry budget?",
    "Action items are tracked in the table above.",
    "We agreed to ship behind a flag and ramp gradually.",
    "Context: this came out of the incident last week.",
    "Next step is to get a +1 from the platform team.",
]
_NOTION_HEADINGS = ["Background", "Decision", "Action items", "Open questions", "Notes"]


def _notion_rich_text(content: str) -> list:
    return [{
        "type": "text",
        "text": {"content": content, "link": None},
        "annotations": {"bold": False, "italic": False, "strikethrough": False,
                        "underline": False, "code": False, "color": "default"},
        "plain_text": content,
        "href": None,
    }]


async def _generate_notion_content(
    conn, run_id: UUID, projects: Sequence[Project], people: Sequence[Person],
    rng: RunRandom, virtual_now: datetime, spec: ProfileSpec,
) -> dict:
    """Seed a Notion integration + databases + pages + blocks + comments,
    emitting a ``notion.page`` timeline event per page."""
    earliest = virtual_now - spec.duration
    span = max(1.0, (virtual_now - earliest).total_seconds())
    span_days = max(1, spec.duration.days)
    plist = list(people)

    workspace_name = (plist[0].email.split("@", 1)[1].split(".")[0].title() + " Workspace") if plist else "Workspace"
    bot_user_id = notion_id()
    integ_pk = uuid4()
    await conn.execute(
        """
        INSERT INTO app_notion.integrations
            (id, run_id, bot_token, workspace_id, workspace_name, bot_user_id,
             bot_name, client_id, client_secret, verification_token)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        integ_pk, run_id, notion_token(), notion_id(), workspace_name, bot_user_id,
        "Ingest Bot", notion_id(), secrets.token_hex(20), notion_verification_token(),
    )

    # Stable per-person Notion user ids (partial-user references on objects).
    person_users = {p.id: notion_id() for p in plist}

    def when() -> datetime:
        return earliest + timedelta(seconds=rng.uniform(0, span))

    async def event(virtual_ts: datetime, payload: dict, actor_id) -> UUID:
        eid = uuid4()
        await conn.execute(
            """
            INSERT INTO timeline.events
                (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical)
            VALUES ($1, $2, $3, 'notion.page', $4, $5::jsonb, '{}'::jsonb, $6)
            """,
            eid, run_id, virtual_ts, actor_id, json.dumps(payload), virtual_ts <= virtual_now,
        )
        return eid

    # Databases: a global wiki + one per project.
    db_specs = [("Team Wiki", None)] + [(p.title, p) for p in projects]
    counts = {"databases": 0, "pages": 0, "blocks": 0, "comments": 0}
    status_schema = {
        "Name": {"id": "title", "name": "Name", "type": "title", "title": {}},
        "Status": {"id": "statU", "name": "Status", "type": "select",
                   "select": {"options": [{"id": f"opt{i}", "name": s, "color": "default"}
                                          for i, s in enumerate(_NOTION_STATUSES)]}},
    }

    pages_per_db = max(3, int(rng.randint(3, 7) * (span_days / 90.0)))

    for db_title, proj in db_specs:
        db_pk = uuid4()
        db_id = notion_id()
        db_created = proj.started_at if proj is not None else earliest
        await conn.execute(
            """
            INSERT INTO app_notion.databases
                (id, integration_pk, database_id, title, parent_type, parent_id, icon,
                 properties_schema, url, created_time, last_edited_time)
            VALUES ($1, $2, $3, $4, 'workspace', NULL, $5, $6::jsonb, $7, $8, $9)
            """,
            db_pk, integ_pk, db_id, db_title, "\U0001F4D8",
            json.dumps(status_schema), f"https://www.notion.so/{db_id.replace('-', '')}",
            db_created, virtual_now,
        )
        counts["databases"] += 1

        svc = (proj.repos[0].split("/")[-1] if proj and proj.repos else db_title.split()[0]).lower()
        for _ in range(pages_per_db):
            author = rng.choice(plist)
            created = when()
            edited = created + timedelta(hours=rng.uniform(0, 24 * 14))
            if edited > virtual_now:
                edited = virtual_now
            title = rng.choice(_NOTION_DOC_TITLES).format(svc=svc)
            page_pk = uuid4()
            page_id = notion_id()
            user_id = person_users[author.id]
            props = {
                "Name": {"id": "title", "type": "title", "title": _notion_rich_text(title)},
                "Status": {"id": "statU", "type": "select",
                           "select": {"name": rng.choice(_NOTION_STATUSES), "color": "default"}},
            }
            ev = await event(created, {"object": "page", "page_id": page_id, "title": title}, author.id)
            await conn.execute(
                """
                INSERT INTO app_notion.pages
                    (id, integration_pk, page_id, parent_type, parent_id, database_pk,
                     title, properties, icon, archived, url, created_by,
                     created_time, last_edited_time, timeline_event_id)
                VALUES ($1, $2, $3, 'database_id', $4, $5, $6, $7::jsonb, NULL, FALSE, $8, $9, $10, $11, $12)
                """,
                page_pk, integ_pk, page_id, db_id, db_pk, title, json.dumps(props),
                f"https://www.notion.so/{page_id.replace('-', '')}", user_id, created, edited, ev,
            )
            counts["pages"] += 1

            # Blocks under the page.
            n_blocks = rng.randint(2, 6)
            brows = []
            for pos in range(n_blocks):
                kind = rng.choice(_NOTION_BLOCK_KINDS) if pos else "heading_2"
                if kind in ("heading_2",):
                    text = rng.choice(_NOTION_HEADINGS)
                else:
                    text = rng.choice(_NOTION_PARAS)
                content = {"rich_text": _notion_rich_text(text), "color": "default"}
                if kind == "to_do":
                    content["checked"] = rng.bool_with_prob(0.4)
                brows.append((
                    uuid4(), page_pk, notion_id(), None, kind, json.dumps(content),
                    False, pos, user_id, created, edited, ev,
                ))
            await conn.executemany(
                """
                INSERT INTO app_notion.blocks
                    (id, page_pk, block_id, parent_block_id, type, content,
                     has_children, position, created_by, created_time, last_edited_time, timeline_event_id)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10, $11, $12)
                """,
                brows,
            )
            counts["blocks"] += len(brows)

            # Comments on the page.
            for _ in range(rng.randint(0, 2)):
                commenter = rng.choice(plist)
                cwhen = edited + timedelta(hours=rng.uniform(0, 48))
                if cwhen > virtual_now:
                    continue
                await conn.execute(
                    """
                    INSERT INTO app_notion.comments
                        (id, page_pk, comment_id, discussion_id, parent_page_id,
                         rich_text, created_by, created_time, last_edited_time)
                    VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9)
                    """,
                    uuid4(), page_pk, notion_id(), notion_id(), page_id,
                    json.dumps(_notion_rich_text(rng.choice([
                        "Nice — left a couple of notes.", "Can we add a rollback plan?",
                        "LGTM once the open question is resolved.", "Bumping this for visibility.",
                    ]))),
                    person_users[commenter.id], cwhen, cwhen,
                )
                counts["comments"] += 1

    return counts


# =============================================================================
# Gmail
# =============================================================================

_GMAIL_SUBJECTS = [
    "Re: {svc} rollout plan", "Weekly update — {team}", "Question about {svc}",
    "Design review: {svc}", "Heads up: {svc} incident", "Offsite logistics",
    "Contract renewal", "Re: hiring pipeline", "{svc} metrics for last week",
    "Action needed: access request", "Notes from the {svc} sync", "Budget planning",
]
_GMAIL_BODIES = [
    "Hi team,\n\nWanted to share a quick update on where things stand. We're on track for the milestone and I'll send a fuller writeup later this week.\n\nThanks",
    "Hey,\n\nCan you take a look at this when you get a chance? Happy to hop on a call if that's easier.\n\nBest",
    "All,\n\nSummarizing the decisions from today: we'll proceed with the phased rollout and revisit the metrics in two weeks.\n\nCheers",
    "Quick one — do we have a rollback plan documented anywhere? Want to make sure before we ship.\n\nThanks!",
    "Following up on the thread below. I think we're aligned; let me know if anything's still open.\n\n—",
]


async def _generate_gmail_content(
    conn, run_id: UUID, projects: Sequence[Project], people: Sequence[Person],
    rng: RunRandom, virtual_now: datetime, spec: ProfileSpec,
) -> dict:
    """Seed the Gmail customer + one mailbox per person, then synthesize email
    threads fanned out to each participant's mailbox (sender SENT, recipients
    INBOX) with RFC-5322 threading headers and a monotonic per-mailbox historyId.
    """
    from email.utils import format_datetime, make_msgid

    if not people:
        return {"mailboxes": 0, "threads": 0, "messages": 0}
    domain = people[0].email.split("@", 1)[1]
    earliest = virtual_now - spec.duration
    span = max(1.0, (virtual_now - earliest).total_seconds())
    span_days = max(1, spec.duration.days)
    plist = list(people)

    sa_priv, sa_pub = generate_rsa_keypair()
    oidc_priv, oidc_pub = generate_rsa_keypair()
    customer_pk = uuid4()
    await conn.execute(
        """
        INSERT INTO app_gmail.customers
            (id, run_id, customer_id, domain, organization_name, service_account_email,
             service_account_public_key, pubsub_oidc_public_key, pubsub_oidc_private_key, pubsub_audience)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        customer_pk, run_id, "C" + secrets.token_hex(4), domain,
        domain.split(".")[0].title(),
        f"gmail-push@{domain.split('.')[0]}-ingest.iam.gserviceaccount.com",
        sa_pub, oidc_pub, oidc_priv,
        f"https://{domain.split('.')[0]}-ingest.example.com/webhooks/gmail/pubsub",
    )

    mailbox_pk: dict[UUID, UUID] = {}
    mbox_rows = []
    for p in plist:
        mp = uuid4()
        mailbox_pk[p.id] = mp
        mbox_rows.append((mp, customer_pk, p.id, p.email))
    await conn.executemany(
        "INSERT INTO app_gmail.mailboxes (id, customer_pk, person_id, email, history_id, profile) "
        "VALUES ($1, $2, $3, $4, 1, '{}'::jsonb)",
        mbox_rows,
    )

    # Each mailbox accumulates instances; we sort by time to assign historyId.
    # instance = (internal_date, logical_thread_id, rfc822_id, in_reply_to, references,
    #             headers, subject, body, label_ids, author_email)
    per_mailbox: dict[UUID, list] = {mp: [] for mp in mailbox_pk.values()}
    # Per (mailbox, logical_thread) → assigned gmail thread_id (stable within mailbox).
    thread_ids: dict[tuple, str] = {}

    n_threads = max(5, int(spec.daily_events * spec.gmail_share * span_days / 3.0))
    others_of = {p.id: [q for q in plist if q.id != p.id] for p in plist}
    counts = {"mailboxes": len(plist), "threads": 0, "messages": 0}

    for _ in range(n_threads):
        initiator = rng.choice(plist)
        pool_others = others_of[initiator.id]
        n_part = min(len(pool_others), rng.randint(1, 3))
        participants = [initiator] + (rng.sample(pool_others, n_part) if n_part else [])
        svc = "core"
        active = [p for p in projects if p.repos]
        if active:
            pr = rng.choice(active)
            svc = pr.repos[0].split("/")[-1]
        subject = rng.choice(_GMAIL_SUBJECTS).format(svc=svc, team=initiator.team_name)
        logical_tid = uuid4().hex
        counts["threads"] += 1

        n_msgs = rng.weighted_pick([(1, 0.4), (2, 0.3), (3, 0.2), (4, 0.1)])
        t0 = earliest + timedelta(seconds=rng.uniform(0, span))
        references: list[str] = []
        prev_msgid = None
        for mi in range(n_msgs):
            author = participants[mi % len(participants)]
            when_msg = t0 + timedelta(hours=rng.uniform(0, 72) * mi)
            if when_msg > virtual_now:
                break
            rfc_id = make_msgid(domain=domain)
            recipients = [q for q in participants if q.id != author.id]
            to_hdr = ", ".join(q.email for q in recipients) or author.email
            body = rng.choice(_GMAIL_BODIES)
            subj = subject if mi == 0 else f"Re: {subject}" if not subject.startswith("Re:") else subject
            base_headers = [
                {"name": "From", "value": f"{author.full_name} <{author.email}>"},
                {"name": "To", "value": to_hdr},
                {"name": "Subject", "value": subj},
                {"name": "Date", "value": format_datetime(when_msg)},
                {"name": "Message-ID", "value": rfc_id},
            ]
            if prev_msgid:
                base_headers.append({"name": "In-Reply-To", "value": prev_msgid})
                base_headers.append({"name": "References", "value": " ".join(references)})

            for part in participants:
                mp = mailbox_pk[part.id]
                labels = ["SENT"] if part.id == author.id else ["INBOX", "UNREAD"]
                per_mailbox[mp].append((
                    when_msg, logical_tid, rfc_id, prev_msgid, list(references),
                    base_headers, subj, body, labels, author.email,
                ))
                counts["messages"] += 1

            references.append(rfc_id)
            prev_msgid = rfc_id

    # Assign per-mailbox historyId in time order, then batch-insert.
    thread_rows: list = []
    msg_rows: list = []
    hist_rows: list = []
    mbox_hid: dict[UUID, int] = {}

    async def _flush():
        nonlocal thread_rows, msg_rows, hist_rows
        if thread_rows:
            await conn.executemany(
                "INSERT INTO app_gmail.threads (id, mailbox_pk, thread_id, subject, snippet) "
                "VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING", thread_rows)
            thread_rows = []
        if msg_rows:
            await conn.executemany(
                """INSERT INTO app_gmail.messages
                    (id, thread_pk, message_id, history_id, rfc822_msg_id, label_ids, headers,
                     snippet, body_plain, body_html, internal_date, size_estimate)
                   VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8,$9,'',$10,$11)""", msg_rows)
            msg_rows = []
        if hist_rows:
            await conn.executemany(
                """INSERT INTO app_gmail.history
                    (mailbox_pk, history_id, history_type, message_id, thread_id, label_ids, occurred_at)
                   VALUES ($1,$2,'messageAdded',$3,$4,$5::jsonb,$6)""", hist_rows)
            hist_rows = []

    thread_pk_by_key: dict[tuple, UUID] = {}
    for mp, instances in per_mailbox.items():
        instances.sort(key=lambda t: t[0])
        hid = 0
        for (when_msg, logical_tid, rfc_id, _irt, _refs, headers, subj, body, labels, _author) in instances:
            hid += 1
            tkey = (mp, logical_tid)
            tpk = thread_pk_by_key.get(tkey)
            if tpk is None:
                tpk = uuid4()
                thread_pk_by_key[tkey] = tpk
                gtid = gmail_thread_id()
                thread_ids[tkey] = gtid
                thread_rows.append((tpk, mp, gtid, subj, body[:100]))
            gtid = thread_ids[tkey]
            gmid = gmail_message_id()
            msg_rows.append((
                uuid4(), tpk, gmid, hid, rfc_id, json.dumps(labels), json.dumps(headers),
                body[:120].replace("\n", " "), body, when_msg, len(body) + 200,
            ))
            hist_rows.append((mp, hid, gmid, gtid, json.dumps(labels), when_msg))
            if len(msg_rows) >= 5000:
                await _flush()
        cur_hid = max(hid, 1)
        await conn.execute(
            "UPDATE app_gmail.mailboxes SET history_id = $1, "
            "profile = jsonb_build_object('emailAddress', email, 'messagesTotal', $2::int, "
            "'threadsTotal', $3::int, 'historyId', $4::text) WHERE id = $5",
            cur_hid, len(instances),
            len({k for k in thread_pk_by_key if k[0] == mp}), str(cur_hid), mp,
        )
    await _flush()
    return counts
