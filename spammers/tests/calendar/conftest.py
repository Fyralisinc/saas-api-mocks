"""Fixtures for the Google Calendar mock fidelity suite.

Reuses the session ``pool``, seeds a deterministic account + two calendars
(alice/bob) and a known set of events on alice's calendar (confirmed + one
cancelled, with staggered ``updated_at`` so incremental/updatedMin filtering is
testable), then wires the Calendar ``state`` singleton + an ASGI client.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from spammers.common.google_token import mint_access_token
from spammers.common.signing import generate_rsa_keypair

DOMAIN = "cal-test.com"
ALICE = f"alice@{DOMAIN}"
BOB = f"bob@{DOMAIN}"
SCOPE = "https://www.googleapis.com/auth/calendar.readonly"

# Event base times (UTC). updated_at is staggered so syncToken/updatedMin filter.
_T0 = datetime(2026, 1, 10, 9, 0, tzinfo=timezone.utc)
EVENTS = [
    # (event_id, status, summary, start_offset_days, updated_offset_hours)
    ("evt0000000000000000000001", "confirmed", "Kickoff", 0, 0),
    ("evt0000000000000000000002", "confirmed", "Design review", 1, 1),
    ("evt0000000000000000000003", "confirmed", "1:1", 2, 2),
    ("evt0000000000000000000004", "cancelled", "Cancelled sync", 3, 3),
]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def cal_run(pool) -> UUID:
    run_id = uuid4()
    await pool.execute(
        """INSERT INTO org.runs (id, size, runtime, seed, fyralis_tenant_id,
               fyralis_base_url, virtual_now, mode, speed_multiplier)
           VALUES ($1,'small','few_months',3,$2,'http://localhost:8000',now(),'frozen',1.0)""",
        run_id, uuid4())
    people = {}
    for handle, email in (("alice", ALICE), ("bob", BOB)):
        pid = uuid4(); people[email] = pid
        await pool.execute(
            """INSERT INTO org.people (id, run_id, handle, full_name, email, role, level, timezone, started_at)
               VALUES ($1,$2,$3,$4,$5,'engineer','mid','UTC',now())""",
            pid, run_id, handle, handle.title(), email)
    priv, pub = generate_rsa_keypair()
    acct_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_calendar.accounts
            (id, run_id, customer_id, domain, service_account_email,
             service_account_client_id, service_account_private_key, service_account_public_key)
           VALUES ($1,$2,'C123',$3,'sa@cal.iam.gserviceaccount.com','cid',$4,$5)""",
        acct_pk, run_id, DOMAIN, priv, pub)
    cal_pks = {}
    for email in (ALICE, BOB):
        cpk = uuid4(); cal_pks[email] = cpk
        await pool.execute(
            """INSERT INTO app_calendar.calendars (id, account_pk, person_id, calendar_id, summary, time_zone)
               VALUES ($1,$2,$3,$4,$5,'UTC')""",
            cpk, acct_pk, people[email], email, f"{email} calendar")
    for eid, status, summary, soff, uoff in EVENTS:
        start = _T0 + timedelta(days=soff)
        updated = _T0 + timedelta(hours=uoff)
        attendees = [{"email": ALICE, "displayName": "Alice", "organizer": True,
                      "self": True, "responseStatus": "accepted"},
                     {"email": BOB, "displayName": "Bob", "responseStatus": "needsAction"}]
        await pool.execute(
            """INSERT INTO app_calendar.events
                (id, calendar_pk, event_id, status, summary, description, location,
                 start_time, end_time, all_day, organizer_email, creator_email, attendees,
                 recurring_event_id, event_type, hangout_link, html_link, sequence, ical_uid,
                 created_at, updated_at)
               VALUES ($1,$2,$3,$4,$5,'','',$6,$7,FALSE,$8,$8,$9::jsonb,NULL,'default',NULL,
                       $10,0,$11,$12,$13)""",
            uuid4(), cal_pks[ALICE], eid, status, summary, start, start + timedelta(minutes=30),
            ALICE, json.dumps(attendees), f"https://cal/{eid}", f"{eid}@google.com",
            start, updated)
    return run_id


@pytest_asyncio.fixture(loop_scope="session")
async def cal_client(pool, cal_run):
    from spammers.calendar import state as cal_state
    from spammers.calendar.app import create_app
    from spammers.calendar.ratelimit import _RL

    cal_state._STATE = cal_state.CalendarMockState(pool=pool, run_id=cal_run)
    _RL._buckets.clear()
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://mock") as c:
        yield c
    cal_state._STATE = None


def cal_token(sub: str = ALICE) -> str:
    tok, _ = mint_access_token(sub, SCOPE)
    return tok


@pytest.fixture
def cal_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {cal_token()}"}
