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
    github_app_id,
    github_installation_id,
    github_repo_id,
    github_sha,
    github_user_id,
    github_webhook_secret,
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

            # Timeline events + message projections (Slack + Discord)
            await _insert_timeline_events(conn, run_id, slack_events, virtual_now)
            await _insert_timeline_events(conn, run_id, discord_events, virtual_now)
            await _project_slack_messages(
                conn, slack_events, virtual_now, workspace_id, channel_ids, slack_user_pks, people,
            )
            await _project_discord_messages(
                conn, discord_events, virtual_now, discord_chan_pks, discord_user_pks,
            )

            await conn.execute(
                "UPDATE org.runs SET finalized_at = now() WHERE id = $1",
                run_id,
            )

    log.info("orggen_done", run_id=str(run_id),
             people=len(people), projects=len(projects),
             slack_events=len(slack_events), discord_events=len(discord_events),
             github_repos=len(github_repos), github_prs=github_counts["pull_requests"])

    return {
        "people": len(people),
        "teams": len(team_names),
        "projects": len(projects),
        "slack_events": len(slack_events),
        "discord_events": len(discord_events),
        "github_repos": len(github_repos),
        **{f"github_{k}": v for k, v in github_counts.items()},
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
