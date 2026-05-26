"""Timeline compiler.

Walks the runtime day by day, emitting typed events per persona and
project. Respects:
  - business hours (9am-6pm in persona's TZ)
  - weekly cycle (Mon-Fri active; Sat/Sun light)
  - per-event-type cadence
  - project lifecycle (events only fire while project is active)

Events are emitted into ``timeline.events`` with ``is_historical = TRUE``
when ``virtual_ts <= run.virtual_now`` at insert time. The Director's
emission loop only emits webhooks for events with ``is_historical = FALSE``
and ``emitted_at IS NULL``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Sequence
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from spammers.orggen.personas import Person
from spammers.orggen.profiles import ProfileSpec
from spammers.orggen.projects import Project
from spammers.orggen.render import render
from spammers.orggen.seed import RunRandom


@dataclass
class TimelineEvent:
    id: UUID
    virtual_ts: datetime
    type: str
    actor_id: UUID
    project_id: UUID | None
    payload: dict
    cross_refs: dict


def _business_hour_offset(rng: RunRandom, tz_name: str) -> timedelta:
    """Pick a UTC offset within business hours for someone in ``tz_name`` today.

    Returns offset from local-9am.
    """
    # 9am .. 6pm = 9 hours window
    minutes = rng.randint(0, 9 * 60 - 1)
    return timedelta(minutes=minutes)


def _local_business_day(rng: RunRandom, day: datetime, tz_name: str) -> datetime | None:
    """Return a UTC datetime inside business hours on the given local day.

    None on weekends (skip).
    """
    tz = ZoneInfo(tz_name)
    local_date = day.astimezone(tz).date()
    weekday = local_date.weekday()    # Mon=0, Sun=6
    if weekday >= 5:
        # Saturday or Sunday — skip with high probability
        if not rng.bool_with_prob(0.1):
            return None
    local_dt = datetime.combine(local_date, time(hour=9), tzinfo=tz) + _business_hour_offset(rng, tz_name)
    return local_dt.astimezone(timezone.utc)


def _daily_event_count(rng: RunRandom, base: int, dayofweek: int) -> int:
    """Sample around the base with weekday variation. Mon-Fri at base, Sat-Sun at 20%."""
    if dayofweek >= 5:
        base = max(1, int(base * 0.2))
    # Gaussian noise around base, ±15%
    n = int(rng.gauss(base, base * 0.15))
    return max(1, n)


def compile_slack_events(
    spec: ProfileSpec,
    rng: RunRandom,
    people: Sequence[Person],
    projects: Sequence[Project],
    *,
    virtual_now: datetime,
) -> list[TimelineEvent]:
    """Generate Slack message events for the entire runtime."""
    rng_s = rng.sub("slack")
    earliest = virtual_now - spec.duration
    slack_daily = int(spec.daily_events * spec.slack_share)
    events: list[TimelineEvent] = []

    # iterate day-by-day
    cursor = earliest
    day_idx = 0
    while cursor < virtual_now:
        dayofweek = cursor.weekday()
        n_today = _daily_event_count(rng_s, slack_daily, dayofweek)
        for i in range(n_today):
            person = rng_s.choice(people)
            # active projects on this day
            active_projects = [p for p in projects if p.started_at <= cursor and (p.ended_at is None or p.ended_at >= cursor)]
            project = rng_s.choice(active_projects) if active_projects and rng_s.bool_with_prob(0.7) else None

            local_when = _local_business_day(rng_s, cursor, person.timezone)
            if local_when is None:
                continue
            if local_when > virtual_now:
                continue

            kind = rng_s.weighted_pick([
                ("banter", 0.30),
                ("standup", 0.15),
                ("work_update", 0.40),
                ("ask", 0.15),
            ])

            if kind == "banter":
                text = render("slack/banter.j2", idx=i + day_idx)
                channel = "#random"
            elif kind == "standup":
                text = render(
                    "slack/standup.j2",
                    persona=person,
                    event_idx=i % 3,
                    yesterday_summary=rng_s.choice([
                        "wrapped up the migration prep",
                        "finished the rate-limiter audit",
                        "shipped the dashboard fix",
                        "investigated the prod alert",
                    ]),
                    today_plan=rng_s.choice([
                        "land the PR + start review queue",
                        "design doc + meetings",
                        "rolling out the new pipeline",
                        "pairing on the auth refactor",
                    ]),
                    blockers="" if rng_s.bool_with_prob(0.7) else rng_s.choice([
                        "blocked on infra access",
                        "waiting on design review",
                        "need a +1 on the spec",
                    ]),
                )
                channel = f"#{person.team_name.lower()}-standup"
            elif kind == "work_update":
                if project is None:
                    text = "shipped a small fix"
                    channel = "#general"
                else:
                    ek = rng_s.choice(["pr_announce", "pr_merged", "ask"])
                    text = render(
                        "slack/work_update.j2",
                        persona=person,
                        event_kind=ek,
                        pr_link=f"https://github.com/{project.repos[0]}/pull/{rng_s.randint(10, 999)}",
                        pr_title=f"{project.slug}: {rng_s.choice(['polish', 'refactor', 'fix', 'speed up', 'consolidate', 'add tests for'])} {rng_s.choice(['edge case', 'hot path', 'shared helper', 'config loader'])}",
                        channel=project.slack_channels[0],
                        incident_summary="api 5xx spike on /v1/events",
                        question="anyone seen this 500 from the gateway?",
                        default_text="update soon",
                    )
                    channel = project.slack_channels[0]
            else:  # ask
                text = render(
                    "slack/work_update.j2",
                    persona=person,
                    event_kind="ask",
                    pr_link="",
                    pr_title="",
                    channel="",
                    incident_summary="",
                    question=rng_s.choice([
                        "what's the canonical way to load tenant context here?",
                        "did anyone see the migration script for 0042?",
                        "is the gateway timeout configurable per route?",
                        "who owns the billing webhook handler now?",
                    ]),
                    default_text="",
                )
                channel = "#help"

            events.append(TimelineEvent(
                id=uuid4(),
                virtual_ts=local_when,
                type="slack.message",
                actor_id=person.id,
                project_id=project.id if project else None,
                payload={
                    "channel": channel,
                    "text": text,
                    "kind": kind,
                },
                cross_refs={},
            ))
        cursor = cursor + timedelta(days=1)
        day_idx += 1

    events.sort(key=lambda e: e.virtual_ts)
    return events


def compile_discord_events(
    spec: ProfileSpec,
    rng: RunRandom,
    people: Sequence[Person],
    projects: Sequence[Project],
    *,
    virtual_now: datetime,
) -> list[TimelineEvent]:
    """Generate Discord message events for the entire runtime.

    Channels mirror what ``_create_discord_application`` seeds: ``general``,
    ``off-topic``, ``dev``, plus one channel per project.
    """
    rng_d = rng.sub("discord")
    earliest = virtual_now - spec.duration
    discord_daily = max(1, int(spec.daily_events * spec.discord_share))
    events: list[TimelineEvent] = []

    cursor = earliest
    day_idx = 0
    while cursor < virtual_now:
        dayofweek = cursor.weekday()
        n_today = _daily_event_count(rng_d, discord_daily, dayofweek)
        for i in range(n_today):
            person = rng_d.choice(people)
            active_projects = [
                p for p in projects
                if p.started_at <= cursor and (p.ended_at is None or p.ended_at >= cursor)
            ]
            project = (
                rng_d.choice(active_projects)
                if active_projects and rng_d.bool_with_prob(0.6) else None
            )

            local_when = _local_business_day(rng_d, cursor, person.timezone)
            if local_when is None or local_when > virtual_now:
                continue

            kind = rng_d.weighted_pick([
                ("banter", 0.35),
                ("work_update", 0.45),
                ("ask", 0.20),
            ])

            if kind == "banter":
                text = render("slack/banter.j2", idx=i + day_idx)
                channel = "off-topic"
            elif kind == "work_update" and project is not None:
                text = render(
                    "slack/work_update.j2",
                    persona=person,
                    event_kind=rng_d.choice(["pr_announce", "pr_merged"]),
                    pr_link=f"https://github.com/{project.repos[0]}/pull/{rng_d.randint(10, 999)}",
                    pr_title=f"{project.slug}: {rng_d.choice(['polish', 'refactor', 'fix', 'speed up'])} {rng_d.choice(['edge case', 'hot path', 'config loader'])}",
                    channel="",
                    incident_summary="",
                    question="",
                    default_text="update soon",
                )
                channel = project.discord_channels[0].lstrip("#")
            elif kind == "work_update":
                text = "shipped a small fix"
                channel = "general"
            else:  # ask
                text = render(
                    "slack/work_update.j2",
                    persona=person,
                    event_kind="ask",
                    pr_link="", pr_title="", channel="", incident_summary="",
                    question=rng_d.choice([
                        "anyone know why the gateway drops the resume?",
                        "what's the intent bit for message content again?",
                        "is there a runbook for the webhook retries?",
                    ]),
                    default_text="",
                )
                channel = "dev"

            events.append(TimelineEvent(
                id=uuid4(),
                virtual_ts=local_when,
                type="discord.message",
                actor_id=person.id,
                project_id=project.id if project else None,
                payload={"channel": channel, "text": text, "kind": kind},
                cross_refs={},
            ))
        cursor = cursor + timedelta(days=1)
        day_idx += 1

    events.sort(key=lambda e: e.virtual_ts)
    return events


_MEETING_DURATIONS = [30, 30, 45, 60, 60, 90]


def compile_calendar_events(
    spec: ProfileSpec,
    rng: RunRandom,
    people: Sequence[Person],
    projects: Sequence[Project],
    *,
    virtual_now: datetime,
) -> list[TimelineEvent]:
    """Generate Google Calendar events for the entire runtime.

    Each event is a meeting the actor *organizes*; one to a handful of other
    people are invited as attendees. It lands on the organizer's calendar at
    projection time. ``actor_id`` is the organizer.
    """
    rng_c = rng.sub("calendar")
    earliest = virtual_now - spec.duration
    cal_daily = max(1, int(spec.daily_events * spec.calendar_share))
    events: list[TimelineEvent] = []

    cursor = earliest
    while cursor < virtual_now:
        dayofweek = cursor.weekday()
        n_today = _daily_event_count(rng_c, cal_daily, dayofweek)
        for _ in range(n_today):
            organizer = rng_c.choice(people)
            active_projects = [
                p for p in projects
                if p.started_at <= cursor and (p.ended_at is None or p.ended_at >= cursor)
            ]
            project = (
                rng_c.choice(active_projects)
                if active_projects and rng_c.bool_with_prob(0.5) else None
            )

            start = _local_business_day(rng_c, cursor, organizer.timezone)
            if start is None or start > virtual_now:
                continue
            duration = rng_c.choice(_MEETING_DURATIONS)

            kind = rng_c.weighted_pick([
                ("one_on_one", 0.25),
                ("team_meeting", 0.30),
                ("project_sync", 0.25),
                ("interview", 0.10),
                ("standup", 0.10),
            ])

            # Invite a plausible set of attendees (always includes the organizer).
            others = [p for p in people if p.id != organizer.id]
            if kind == "one_on_one":
                n_inv = 1
            elif kind == "interview":
                n_inv = rng_c.randint(1, 3)
            elif kind == "standup":
                n_inv = min(len(others), rng_c.randint(2, 6))
            else:
                n_inv = min(len(others), rng_c.randint(2, 5))
            invited = rng_c.sample(others, n_inv) if others and n_inv else []
            attendee_ids = [str(organizer.id)] + [str(p.id) for p in invited]

            if kind == "one_on_one" and invited:
                summary = f"{organizer.full_name.split()[0]} / {invited[0].full_name.split()[0]} 1:1"
            elif kind == "team_meeting":
                summary = f"{organizer.team_name} {rng_c.choice(['weekly', 'sync', 'planning', 'retro'])}"
            elif kind == "project_sync" and project is not None:
                summary = f"{project.title} sync"
            elif kind == "interview":
                summary = f"Interview — {rng_c.choice(['Backend', 'Frontend', 'Platform', 'Data', 'SRE'])} candidate"
            elif kind == "standup":
                summary = f"{organizer.team_name} standup"
            else:
                summary = rng_c.choice(["Design review", "Roadmap check-in", "Bug triage", "Architecture chat"])

            events.append(TimelineEvent(
                id=uuid4(),
                virtual_ts=start,
                type="calendar.event",
                actor_id=organizer.id,
                project_id=project.id if project else None,
                payload={
                    "kind": kind,
                    "summary": summary,
                    "duration_mins": duration,
                    "attendee_ids": attendee_ids,
                    "location": rng_c.choice(["", "", "Zoom", "Meet", "Conf Room A", "Conf Room B"]),
                },
                cross_refs={},
            ))
        cursor = cursor + timedelta(days=1)

    events.sort(key=lambda e: e.virtual_ts)
    return events
