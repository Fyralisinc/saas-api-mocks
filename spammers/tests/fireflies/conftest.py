"""Fixtures for the Fireflies mock fidelity suite (the REAL GraphQL contract).

Fireflies is POLL + webhook PUSH. Seeds a deterministic workspace with six
meeting TRANSCRIPTS over ~6 months — enough to walk a multi-page skip/limit cursor
(limit 2 → 3 pages) and exercise the epoch-millis ``date`` + ISO-Z ``dateString`` +
minutes ``duration``, the nested ``summary``/``meeting_attendees``/``sentences``
shapes, and the ``fromDate``/``toDate`` filters. Wires the Fireflies ``state``
singleton + an ASGI client.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

API_TOKEN = "ff_fidelityMockToken00000000000000000000000000000000"
WEBHOOK_SECRET = "ffwh_fidelitymocksecret00000000000000"
OWNER_USER_ID = "Owner00xYz"
OWNER_EMAIL = "founder@alpenlabs.io"
OWNER_NAME = "Avery Founder"

VNOW = datetime(2026, 2, 1, tzinfo=timezone.utc)

# (transcript_id, title, days_ago, duration_minutes, n_participants, mtype)
TRANSCRIPTS = [
    ("ASxwZxCstx", "Engineering Standup", 200, 15.0, 5, "standup"),
    ("Bf3KmQp9Lr", "Sprint 7 Planning", 160, 60.0, 6, "planning"),
    ("Cz8WnT2hVq", "1:1 — Avery / Sam", 120, 30.0, 2, "one_on_one"),
    ("Dk5RpL7mXc", "All-Hands — November 2025", 80, 45.0, 12, "all_hands"),
    ("Em2YtB9wNs", "Design Review: billing v2", 40, 45.0, 4, "design_review"),
    ("Fn7QsV4kZp", "Customer Call: Acme Corp", 10, 30.0, 3, "customer_call"),
]


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def fireflies_run(pool) -> UUID:
    run_id = uuid4()
    await pool.execute(
        """INSERT INTO org.runs (id, size, runtime, seed, fyralis_tenant_id,
               fyralis_base_url, virtual_now, mode, speed_multiplier)
           VALUES ($1,'small','few_months',47,$2,'http://localhost:8000',$3,'frozen',1.0)""",
        run_id, uuid4(), VNOW)
    ws_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_fireflies.workspaces
            (id, run_id, base_url, team_name, api_token, webhook_secret,
             owner_user_id, owner_email, owner_name, created_at)
           VALUES ($1,$2,'https://api.fireflies.ai','Alpen Labs',$3,$4,$5,$6,$7,$8)""",
        ws_pk, run_id, API_TOKEN, WEBHOOK_SECRET, OWNER_USER_ID, OWNER_EMAIL,
        OWNER_NAME, VNOW - timedelta(days=400))

    for i, (tid, title, days, dur, npart, mtype) in enumerate(TRANSCRIPTS):
        mdate = VNOW - timedelta(days=days)
        emails = [f"teammate{j}@alpenlabs.io" for j in range(npart)]
        attendees = [{"displayName": f"Teammate {j}", "email": e, "name": f"Teammate {j}",
                      "phoneNumber": None, "location": None}
                     for j, e in enumerate(emails)]
        speakers = [{"id": f"spk{j}", "name": f"Teammate {j}"} for j in range(npart)]
        summary = {
            "overview": f"The team met for a {mtype} and discussed progress.",
            "action_items": [f"Follow up after {title}."],
            "keywords": ["roadmap", "billing"],
            "meeting_type": mtype, "topics_discussed": ["status", "next steps"],
            "short_summary": f"{title}.",
        }
        sentences = [{
            "index": 0, "speaker_name": "Teammate 0", "speaker_id": "spk0",
            "text": "Let's get started.", "raw_text": "Let's get started.",
            "start_time": 0.0, "end_time": 5.5,
            "ai_filters": {"task": None, "sentiment": "neutral"},
        }]
        await pool.execute(
            """INSERT INTO app_fireflies.transcripts
                (id, workspace_pk, transcript_id, title, meeting_date, duration_minutes,
                 organizer_email, host_email, participants, fireflies_users,
                 meeting_attendees, speakers, summary, sentences, meeting_info,
                 calendar_id, transcript_url, audio_url, video_url, meeting_link,
                 client_reference_id, version, sort_key, is_historical)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$7,$8::jsonb,$9::jsonb,$10::jsonb,$11::jsonb,
                       $12::jsonb,$13::jsonb,$14::jsonb,$15,$16,$17,$18,$19,$20,1,$21,TRUE)""",
            uuid4(), ws_pk, tid, title, mdate, dur, emails[0],
            json.dumps(emails), json.dumps(emails[:1]), json.dumps(attendees),
            json.dumps(speakers), json.dumps(summary), json.dumps(sentences),
            json.dumps({"fred_joined": True, "silent_meeting": False,
                        "summary_status": "processed"}),
            f"cal_{tid}", f"https://app.fireflies.ai/view/{tid}",
            f"https://api.fireflies.ai/audio/{tid}.mp3",
            f"https://api.fireflies.ai/video/{tid}.mp4",
            "https://meet.google.com/abc-defg-hij", f"cref-{i}", i)
    return run_id


@pytest_asyncio.fixture(loop_scope="session")
async def fireflies_client(pool, fireflies_run):
    from spammers.fireflies import state as ff_state
    from spammers.fireflies.app import create_app, _FORCED_429

    ff_state._STATE = ff_state.FirefliesMockState(pool=pool, run_id=fireflies_run)
    _FORCED_429["count"] = 0
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://mock") as c:
        yield c
    ff_state._STATE = None


@pytest.fixture
def fireflies_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_TOKEN}", "Content-Type": "application/json"}
