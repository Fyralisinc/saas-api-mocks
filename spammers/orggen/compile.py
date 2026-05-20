"""End-to-end OrgGen compiler.

Inputs:  org.runs row + the linked profile spec
Outputs: populates org.teams, org.people, org.projects, timeline.events,
         app_slack.{workspaces,channels,users,messages}

Idempotent on the run_id: clears and regenerates.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Sequence
from uuid import UUID, uuid4

import asyncpg
import structlog

from spammers.common.ids import (
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
from spammers.orggen.personas import Person, generate_people
from spammers.orggen.profiles import ProfileSpec
from spammers.orggen.projects import Project, generate_projects
from spammers.orggen.seed import RunRandom
from spammers.orggen.timeline import TimelineEvent, compile_slack_events


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

    # 3. Timeline (Slack only this turn; others follow when their mocks land)
    slack_events = compile_slack_events(spec, rng, people, projects, virtual_now=virtual_now)

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

            # Timeline events + Slack message projections
            await _insert_timeline_events(conn, run_id, slack_events, virtual_now)
            await _project_slack_messages(
                conn, slack_events, virtual_now, workspace_id, channel_ids, slack_user_pks, people,
            )

            await conn.execute(
                "UPDATE org.runs SET finalized_at = now() WHERE id = $1",
                run_id,
            )

    log.info("orggen_done", run_id=str(run_id),
             people=len(people), projects=len(projects),
             slack_events=len(slack_events))

    return {
        "people": len(people),
        "teams": len(team_names),
        "projects": len(projects),
        "slack_events": len(slack_events),
    }


async def _clear_existing(conn, run_id: UUID) -> None:
    # Cascading deletes from org.runs would also work, but we want to keep
    # the run row.  We delete what we know we generate.
    await conn.execute("DELETE FROM timeline.events WHERE run_id = $1", run_id)
    await conn.execute(
        "DELETE FROM app_slack.workspaces WHERE run_id = $1", run_id,
    )
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
