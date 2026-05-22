"""Live event generator.

While the historical timeline (compile.py) populates events backwards from
virtual_now and marks them is_historical=TRUE (pull-API only), this module
generates NEW events that are dated forward of virtual_now and marked
is_historical=FALSE — the emission loop picks them up and delivers them as
signed webhooks.

Two entry points:
  - ``inject_slack_message(...)``  — one-off, fully parameterized
  - ``LiveEventGenerator(...)``    — long-running, samples at a target rate
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

import asyncpg
import structlog

from spammers.common.clock import get_clock
from spammers.common.ids import github_sha
from spammers.orggen.profiles import resolve
from spammers.orggen.render import render
from spammers.orggen.seed import RunRandom


log = structlog.get_logger("spammers.orggen.live")


async def inject_github_event(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    kind: str = "pull_request",
    repo: Optional[str] = None,
    handle: Optional[str] = None,
    title: Optional[str] = None,
    at_virtual: Optional[datetime] = None,
) -> UUID:
    """Create a live GitHub entity (``pull_request`` or ``issues``) + a
    not-historical timeline event the emission loop will webhook.

    The entity is projected immediately (so REST reads see it); the event drives
    the outbound webhook. Returns the new timeline event id.
    """
    if kind not in ("pull_request", "issues"):
        raise ValueError(f"unsupported kind: {kind}")

    person = await pool.fetchrow(
        "SELECT id, handle FROM org.people WHERE run_id = $1 AND ($2::text IS NULL OR handle = $2) "
        "ORDER BY (handle = $2) DESC, random() LIMIT 1",
        run_id, handle,
    )
    if person is None:
        raise LookupError("no people in this run; did you forget `prepare`?")

    repo_row = await pool.fetchrow(
        """
        SELECT r.id, r.owner, r.name, r.full_name
          FROM app_github.repositories r
          JOIN app_github.installations inst ON inst.id = r.installation_pk
          JOIN app_github.apps a ON a.id = inst.app_pk
         WHERE a.run_id = $1 AND ($2::text IS NULL OR r.name = $2 OR r.full_name = $2)
         ORDER BY r.name LIMIT 1
        """,
        run_id, repo,
    )
    if repo_row is None:
        raise LookupError("no github repositories in this run; did you forget `prepare`?")

    clock = await get_clock(pool, run_id)
    when = at_virtual or clock.virtual_now
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    number = (await pool.fetchval(
        """
        SELECT COALESCE(MAX(n), 0) + 1 FROM (
            SELECT number AS n FROM app_github.pull_requests WHERE repo_pk = $1
            UNION ALL
            SELECT number AS n FROM app_github.issues WHERE repo_pk = $1
        ) s
        """,
        repo_row["id"],
    ))

    etype = f"github.{kind}"
    event_id = uuid4()
    await pool.execute(
        """
        INSERT INTO timeline.events (id, run_id, virtual_ts, type, actor_id, payload, is_historical)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, FALSE)
        """,
        event_id, run_id, when, etype, person["id"],
        json.dumps({"action": "opened", "repo": repo_row["full_name"], "number": number}),
    )

    if kind == "pull_request":
        await pool.execute(
            """
            INSERT INTO app_github.pull_requests
                (id, repo_pk, number, title, body, state, merged, user_login, head_ref,
                 head_sha, base_sha, created_at, updated_at, timeline_event_id)
            VALUES ($1,$2,$3,$4,$5,'open',FALSE,$6,$7,$8,$9,$10,$10,$11)
            """,
            uuid4(), repo_row["id"], number, title or f"Live PR #{number}", "Injected live.",
            person["handle"], f"feature/{repo_row['name']}-{number}", github_sha(),
            github_sha(), when, event_id,
        )
    else:
        await pool.execute(
            """
            INSERT INTO app_github.issues
                (id, repo_pk, number, title, body, state, user_login, created_at, updated_at, timeline_event_id)
            VALUES ($1,$2,$3,$4,$5,'open',$6,$7,$7,$8)
            """,
            uuid4(), repo_row["id"], number, title or f"Live issue #{number}", "Injected live.",
            person["handle"], when, event_id,
        )

    return event_id


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


async def inject_github_event(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    kind: str = "pull_request",
    repo: Optional[str] = None,
    handle: Optional[str] = None,
    title: Optional[str] = None,
    at_virtual: Optional[datetime] = None,
) -> UUID:
    """Create a live GitHub entity (``pull_request`` or ``issues``) and a
    not-historical timeline event so the emission loop webhooks it.

    The entity is projected immediately (the resource exists when opened), so
    REST reads see it right away; the webhook notifies the consumer. Returns the
    new event id.
    """
    if kind not in ("pull_request", "issues"):
        raise ValueError(f"unsupported github inject kind: {kind!r}")

    # Resolve the installation's first repo (or the named one) for this run.
    repo_row = await pool.fetchrow(
        """
        SELECT r.id, r.owner, r.name FROM app_github.repositories r
          JOIN app_github.installations inst ON inst.id = r.installation_pk
          JOIN app_github.apps a ON a.id = inst.app_pk
         WHERE a.run_id = $1 AND ($2::text IS NULL OR r.name = $2 OR (r.owner || '/' || r.name) = $2)
         ORDER BY r.name
         LIMIT 1
        """,
        run_id, repo,
    )
    if repo_row is None:
        raise LookupError(f"no github repo for run {run_id} (repo={repo!r})")
    repo_pk, owner, name = repo_row["id"], repo_row["owner"], repo_row["name"]
    full = f"{owner}/{name}"

    # Actor: a real person on this run (timeline.events.actor_id is NOT NULL).
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
    actor_id, login = person["id"], person["handle"]

    # PRs and issues share one number sequence per repo.
    next_num = await pool.fetchval(
        """
        SELECT COALESCE(MAX(number), 0) + 1 FROM (
            SELECT number FROM app_github.pull_requests WHERE repo_pk = $1
            UNION ALL
            SELECT number FROM app_github.issues WHERE repo_pk = $1
        ) s
        """,
        repo_pk,
    )

    clock = await get_clock(pool, run_id)
    when = at_virtual or clock.virtual_now
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    etype = f"github.{kind}"
    event_id = uuid4()
    payload = {"repo": full, "number": next_num, "action": "opened"}
    await pool.execute(
        """
        INSERT INTO timeline.events
            (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, '{}'::jsonb, FALSE)
        """,
        event_id, run_id, when, etype, actor_id, json.dumps(payload),
    )

    if kind == "pull_request":
        await pool.execute(
            """
            INSERT INTO app_github.pull_requests
                (id, repo_pk, number, title, body, state, merged, user_login,
                 head_ref, head_sha, base_sha, created_at, updated_at, timeline_event_id)
            VALUES ($1, $2, $3, $4, '', 'open', FALSE, $5, $6, $7, $8, $9, $9, $10)
            """,
            uuid4(), repo_pk, next_num, title or f"Live PR #{next_num}",
            login, f"feature/live-{next_num}", github_sha(), github_sha(), when, event_id,
        )
    else:  # issues
        await pool.execute(
            """
            INSERT INTO app_github.issues
                (id, repo_pk, number, title, body, state, user_login,
                 created_at, updated_at, timeline_event_id)
            VALUES ($1, $2, $3, $4, '', 'open', $5, $6, $6, $7)
            """,
            uuid4(), repo_pk, next_num, title or f"Live issue #{next_num}", login, when, event_id,
        )

    return event_id


class LiveEventGenerator:
    """Long-running generator that produces live slack messages.

    Targets ``msgs_per_minute`` events per minute (drawn from a Poisson-ish
    distribution). Each event gets a virtual_ts slightly ahead of the current
    virtual_now so the emission loop picks it up on the next tick.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        run_id: UUID,
        *,
        msgs_per_minute: float = 6.0,
        seed_extra: int = 0,
    ) -> None:
        self._pool = pool
        self._run_id = run_id
        self._msgs_per_minute = msgs_per_minute
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._seed_extra = seed_extra

    async def _loop(self) -> None:
        # mean wait between events = 60 / rate
        # We pick from people/projects deterministically per run + a tick counter
        row = await self._pool.fetchrow(
            "SELECT size, runtime, seed FROM org.runs WHERE id = $1",
            self._run_id,
        )
        if row is None:
            return
        spec = resolve(row["size"], row["runtime"])
        rng = RunRandom(int(row["seed"]) + self._seed_extra, "live")

        people = [dict(r) for r in await self._pool.fetch(
            "SELECT id, handle, team_name FROM org.people p "
            "LEFT JOIN org.teams t ON t.id = p.team_id "
            "WHERE p.run_id = $1",
            self._run_id,
        )]
        if not people:
            return

        tick = 0
        while not self._stop.is_set():
            tick += 1
            interval = max(0.2, 60.0 / self._msgs_per_minute * rng.uniform(0.5, 1.5))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                pass

            person = rng.choice(people)
            text = render(
                "slack/work_update.j2",
                persona=type("P", (), {"voice_signature": {"formality": "casual"}})(),
                event_kind="ask",
                pr_link="",
                pr_title="",
                channel="",
                incident_summary="",
                question=rng.choice([
                    "anyone seen the build break on main?",
                    "is the cache ttl configurable from env?",
                    "what's the canonical way to log a tenant id?",
                    "do we have a runbook for the gateway timeout?",
                ]),
                default_text="",
            )
            try:
                await inject_slack_message(
                    self._pool,
                    self._run_id,
                    handle=person["handle"],
                    channel="#general",
                    text=text,
                )
            except Exception as exc:
                log.warning("live_inject_failed", error=str(exc))

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None
