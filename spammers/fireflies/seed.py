"""Realistic Fireflies corpus seeding.

Fireflies is a NET-NEW Tier-C source: the frozen run has no Fireflies corpus, so
we model realistic content ourselves (the brief sanctions this, like ramp/linkedin
projecting the run's people). Fireflies is the company's **AI meeting-notetaker** —
it transcribes meetings — so we project the run's ``org.people`` into a stream of
recurring meeting TRANSCRIPTS over ~14 months:

  * a weekly **engineering standup**, a bi-weekly **sprint planning**, a monthly
    **all-hands**, rotating **1:1s**, and a sprinkling of **design reviews** /
    **customer calls** — each attributed to a subset of people as participants /
    attendees / speakers, with a realistic summary (overview, action items,
    keywords) + a handful of representative transcript sentences.

The newest-first list order is the ``sort_key`` (assigned in ascending date order,
so the most recent meeting has the highest key). A Transcript's wire ``date`` is a
Float epoch-MILLISECONDS; ``duration`` is in MINUTES. Everything is deterministic
off the run seed. Idempotent: a second call after the workspace row exists is a
no-op.
"""
from __future__ import annotations

import string
from datetime import datetime, timedelta, timezone
from random import Random
from typing import Optional
from uuid import UUID, uuid4

import asyncpg

# Seed-stable workspace identity (hand these to the ingest-client / memory).
TEAM_NAME = "Alpen Labs"
API_TOKEN = "ff_3f9a2c8e1b7d4506a9c2e8f1b7d45063aGmZ4kP9xWqT2sLcV7nR8yB3"
WEBHOOK_SECRET = "ffwh_5f3a9c2e7b1d40689c2e8f1b7d450632"

_ALNUM = string.ascii_letters + string.digits
_KEYWORDS = ["roadmap", "billing", "latency", "onboarding", "infra", "hiring",
             "pricing", "API", "migration", "incident", "design", "metrics",
             "retention", "growth", "security", "compliance"]
_TOPICS = ["Q3 roadmap", "billing v2", "API latency", "customer onboarding",
           "infra migration", "hiring plan", "incident retro", "pricing model",
           "design review", "OKR check-in", "data pipeline", "release planning"]


def _ff_id(rng: Random) -> str:
    """A Fireflies-style short id — ~10 mixed-case alnum chars (e.g. 'ASxwZxCstx')."""
    return "".join(rng.choice(_ALNUM) for _ in range(10))


def _sentences(rng: Random, speakers: list[tuple[str, str]], topic: str) -> list[dict]:
    """A handful of representative transcript sentences (NOT the full body)."""
    openers = [
        f"Okay, let's get started — today we're focused on {topic}.",
        f"Quick update from my side on {topic}.",
        "I think the main blocker is still the rollout timeline.",
        "Can we get a number on where we are this week?",
        "Let's make sure we follow up on the action items from last time.",
        f"I'll take the {topic} piece and circle back by Friday.",
        "Sounds good, let's lock that in.",
    ]
    out: list[dict] = []
    t = 0.0
    n = rng.randint(3, 6)
    for i in range(n):
        sid, sname = rng.choice(speakers)
        text = rng.choice(openers)
        dur = rng.uniform(4.0, 18.0)
        out.append({
            "index": i,
            "speaker_name": sname,
            "speaker_id": sid,
            "text": text,
            "raw_text": text,
            "start_time": round(t, 2),
            "end_time": round(t + dur, 2),
            "ai_filters": {
                "task": None, "pricing": None, "metric": None, "question": None,
                "date_and_time": None,
                "sentiment": rng.choice(["positive", "neutral", "neutral"]),
            },
        })
        t += dur + rng.uniform(0.5, 2.0)
    return out


def _summary(rng: Random, mtype: str, topic: str, names: list[str]) -> dict:
    actions = [
        f"{rng.choice(names)} to draft the {topic} proposal.",
        f"{rng.choice(names)} to follow up with the customer by EOW.",
        f"Schedule a deep-dive on {topic} next sprint.",
    ]
    kws = rng.sample(_KEYWORDS, k=rng.randint(3, 5))
    return {
        "keywords": kws,
        "action_items": rng.sample(actions, k=rng.randint(1, 3)),
        "outline": [f"Intro", f"{topic} discussion", "Action items"],
        "shorthand_bullet": [f"Discussed {topic}", "Aligned on next steps"],
        "overview": f"The team met for a {mtype} and discussed {topic}, "
                    f"reviewed progress, and agreed on follow-ups.",
        "bullet_gist": [f"{topic} progress reviewed", "Owners assigned to action items"],
        "gist": f"{mtype}: {topic}",
        "short_summary": f"A {mtype} covering {topic} with assigned follow-ups.",
        "short_overview": f"{mtype} on {topic}.",
        "meeting_type": mtype,
        "topics_discussed": rng.sample(_TOPICS, k=rng.randint(2, 4)),
        "transcript_chapters": [],
    }


async def seed_fireflies(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    at: Optional[datetime] = None,
) -> dict[str, int]:
    """Provision the workspace + a meeting-transcript stream.

    Idempotent. Returns ``{"transcripts": N}``."""
    existing = await pool.fetchval(
        "SELECT id FROM app_fireflies.workspaces WHERE run_id = $1", run_id)
    if existing is not None:
        return {"transcripts": 0}

    seed_row = await pool.fetchrow(
        "SELECT seed, virtual_now FROM org.runs WHERE id = $1", run_id)
    if seed_row is None:
        raise LookupError(f"no run {run_id}")
    rng = Random(int(seed_row["seed"]) ^ 0x66_69_72_65)  # 'fire'
    now = at or seed_row["virtual_now"] or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    people = await pool.fetch(
        "SELECT handle, full_name, email, role, started_at FROM org.people "
        "WHERE run_id = $1 ORDER BY started_at, handle", run_id)
    # Build a roster of (user_id, name, email) — fall back to synthetic if empty.
    roster: list[tuple[str, str, str]] = []
    for p in people:
        full = (p["full_name"] or p["handle"] or "Teammate").strip()
        email = p["email"] or f"{(p['handle'] or 'user')}@alpenlabs.io"
        roster.append((_ff_id(rng), full, email))
    if not roster:
        for i in range(6):
            roster.append((_ff_id(rng), f"Teammate {i}", f"teammate{i}@alpenlabs.io"))

    owner_id, owner_name, owner_email = roster[0]

    ws_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_fireflies.workspaces
            (id, run_id, base_url, team_name, api_token, webhook_secret,
             owner_user_id, owner_email, owner_name, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
        ws_pk, run_id, "https://api.fireflies.ai", TEAM_NAME, API_TOKEN,
        WEBHOOK_SECRET, owner_id, owner_email, owner_name, now - timedelta(days=460))

    # ---- Build the meeting schedule -----------------------------------------
    # Fixed adoption date: Fireflies (AI notetaker) is adopted LATER than the
    # core tools — once the team is larger and most meetings are remote. Anchoring
    # here (not a rolling `now - 430d` window) means the transcript history starts
    # at a deliberate adoption date and accumulates forward as virtual-now advances.
    _ADOPTED_FF = datetime(2025, 1, 1, tzinfo=timezone.utc)
    start = _ADOPTED_FF if _ADOPTED_FF < now else now - timedelta(days=14)
    meetings: list[tuple[datetime, str, str, float, int]] = []

    def _slot(d: datetime, hour: int) -> datetime:
        return d.replace(hour=hour, minute=0, second=0, microsecond=0)

    day = start
    week = 0
    while day < now - timedelta(days=1):
        wd = day.weekday()
        if wd == 0:  # Monday — weekly engineering standup
            meetings.append((_slot(day, 9), "Engineering Standup", "standup", 15.0,
                             min(len(roster), rng.randint(5, 9))))
        if wd == 1 and week % 2 == 0:  # alt Tuesday — sprint planning
            meetings.append((_slot(day, 10), f"Sprint {week // 2 + 1} Planning",
                             "planning", 60.0, min(len(roster), rng.randint(5, 8))))
        if wd == 3:  # Thursday — a rotating 1:1
            a = roster[0]
            b = rng.choice(roster[1:]) if len(roster) > 1 else roster[0]
            meetings.append((_slot(day, 14), f"1:1 — {a[1].split()[0]} / {b[1].split()[0]}",
                             "one_on_one", 30.0, 2))
        if wd == 2 and day.day <= 7:  # first Wednesday — monthly all-hands
            meetings.append((_slot(day, 11), f"All-Hands — {day.strftime('%B %Y')}",
                             "all_hands", 45.0, min(len(roster), rng.randint(10, 20))))
        if wd == 4 and rng.random() < 0.35:  # some Fridays — design review / customer call
            kind = rng.choice([("Design Review", "design_review"),
                               ("Customer Call", "customer_call")])
            meetings.append((_slot(day, 13), f"{kind[0]}: {rng.choice(_TOPICS)}",
                             kind[1], rng.choice([30.0, 45.0]),
                             min(len(roster), rng.randint(3, 6))))
        if wd == 6:
            week += 1
        day += timedelta(days=1)

    meetings.sort(key=lambda m: m[0])

    # ---- Materialize transcripts (sort_key ascending with date) -------------
    count = 0
    for i, (mdate, title, mtype, duration, n_part) in enumerate(meetings):
        parts = rng.sample(roster, k=min(n_part, len(roster)))
        if parts[0][0] != owner_id and rng.random() < 0.5:
            parts[0] = roster[0]  # owner often hosts
        speakers = [(uid, name) for uid, name, _e in parts]
        names = [name.split()[0] for _u, name, _e in parts]
        emails = [e for _u, _n, e in parts]
        attendees = [{
            "displayName": name, "email": email, "name": name,
            "phoneNumber": None, "location": None,
        } for _u, name, email in parts]
        organizer = parts[0][2]
        tid = _ff_id(rng)
        cref = f"cal-{uuid4().hex[:12]}"
        await pool.execute(
            """INSERT INTO app_fireflies.transcripts
                (id, workspace_pk, transcript_id, title, meeting_date, duration_minutes,
                 organizer_email, host_email, participants, fireflies_users,
                 meeting_attendees, speakers, summary, sentences, meeting_info,
                 calendar_id, transcript_url, audio_url, video_url, meeting_link,
                 client_reference_id, version, sort_key, is_historical)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb,$11::jsonb,
                       $12::jsonb,$13::jsonb,$14::jsonb,$15::jsonb,$16,$17,$18,$19,
                       $20,$21,1,$22,TRUE)""",
            uuid4(), ws_pk, tid, title, mdate, duration,
            organizer, organizer,
            _json(emails), _json(emails[: max(1, len(emails) // 2)]),
            _json(attendees),
            _json([{"id": uid, "name": name} for uid, name in speakers]),
            _json(_summary(rng, mtype, rng.choice(_TOPICS), names)),
            _json(_sentences(rng, speakers, rng.choice(_TOPICS))),
            _json({"fred_joined": True, "silent_meeting": False,
                   "summary_status": "processed"}),
            f"cal_{uuid4().hex[:16]}",
            f"https://app.fireflies.ai/view/{tid}",
            f"https://api.fireflies.ai/audio/{tid}.mp3",
            f"https://api.fireflies.ai/video/{tid}.mp4",
            "https://meet.google.com/" + "-".join(
                "".join(rng.choice(string.ascii_lowercase) for _ in range(k))
                for k in (3, 4, 3)),
            cref, i)
        count += 1

    return {"transcripts": count}


def _json(v) -> str:
    import json
    return json.dumps(v)
