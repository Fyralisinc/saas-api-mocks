"""Fixtures for the Ashby mock fidelity suite (RPC .list/.info + webhook).

Seeds a deterministic organization with a small, fully-controlled recruiting set:
3 candidates, 5 applications (a mix of Active / Hired / Archived / Lead with
staggered ``updated_at`` so the cursor walk paginates and the syncToken floor is
testable), 2 jobs, 2 interview definitions, and 2 offers. Wires the Ashby ``state``
singleton + an ASGI client. The API key is the HTTP Basic *username* (empty pw).
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from spammers.ashby import dto as _dto

API_KEY = "ashby_live_fidelityMockKey_abc123XYZ"
WEBHOOK_SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
LEGAL_NAME = "Alpen Labs Inc."
ORG_ID = "alpenlabs"

# Fixed virtual clock so updated_at windows + the minted syncToken are deterministic.
VNOW = datetime(2026, 2, 1, tzinfo=timezone.utc)

CAND = [UUID(f"c0000000-0000-4000-8000-00000000000{i}") for i in range(1, 4)]
JOB = [UUID(f"30000000-0000-4000-8000-00000000000{i}") for i in range(1, 3)]
APP = [UUID(f"a0000000-0000-4000-8000-00000000000{i}") for i in range(1, 6)]
INT = [UUID(f"10000000-0000-4000-8000-00000000000{i}") for i in range(1, 3)]
OFF = [UUID(f"f0000000-0000-4000-8000-00000000000{i}") for i in range(1, 3)]

# (app uuid, status, days-before-VNOW for updated_at)
APP_SPEC = [
    (APP[0], "Active", 9),
    (APP[1], "Active", 7),
    (APP[2], "Hired", 5),
    (APP[3], "Archived", 3),
    (APP[4], "Lead", 1),     # most-recent → the high-water the syncToken pins
]


def basic_header(key: str = API_KEY) -> dict[str, str]:
    tok = base64.b64encode(f"{key}:".encode()).decode()
    return {"Authorization": f"Basic {tok}"}


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def ashby_run(pool) -> UUID:
    run_id = uuid4()
    await pool.execute(
        """INSERT INTO org.runs (id, size, runtime, seed, fyralis_tenant_id,
               fyralis_base_url, virtual_now, mode, speed_multiplier)
           VALUES ($1,'small','few_months',17,$2,'http://localhost:8000',$3,'frozen',1.0)""",
        run_id, uuid4(), VNOW)
    org_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_ashby.organizations
            (id, run_id, base_url, org_id, legal_business_name, api_key, webhook_secret,
             created_at)
           VALUES ($1,$2,'https://api.ashbyhq.com',$3,$4,$5,$6,$7)""",
        org_pk, run_id, ORG_ID, LEGAL_NAME, API_KEY, WEBHOOK_SECRET,
        VNOW - timedelta(days=900))

    async def ins(kind, ent_id, status, data, created, updated):
        await pool.execute(
            """INSERT INTO app_ashby.entities
                (id, org_pk, kind, entity_id, status, data, created_at, updated_at,
                 is_historical)
               VALUES ($1,$2,$3,$1,$4,$5::jsonb,$6,$7,TRUE)""",
            ent_id, org_pk, kind, status, json.dumps(data), created, updated)

    # jobs
    for i, jid in enumerate(JOB):
        opened = VNOW - timedelta(days=120 + i * 30)
        status = "Open" if i == 0 else "Closed"
        closed = None if i == 0 else opened + timedelta(days=40)
        data = _dto.job_dto(
            entity_id=str(jid), title=["Senior Backend Engineer", "Product Designer"][i],
            status=status, employment_type="FullTime", location_id=str(uuid4()),
            department_id=str(uuid4()), default_interview_plan_id=str(uuid4()),
            interview_plan_ids=[str(uuid4())], job_posting_ids=[str(uuid4())],
            hiring_team=[], created_at=opened, updated_at=closed or (VNOW - timedelta(days=12)),
            opened_at=opened, closed_at=closed)
        await ins("job", jid, status, data, opened, closed or (VNOW - timedelta(days=12)))

    # interview definitions (no wire timestamps; internal cols derived from a job)
    for i, iid in enumerate(INT):
        data = _dto.interview_dto(
            entity_id=str(iid), title=["Technical Phone Screen", "Onsite Debrief"][i],
            job_id=str(JOB[0]), feedback_form_definition_id=str(uuid4()),
            is_debrief=(i == 1))
        await ins("interview", iid, None, data, VNOW - timedelta(days=100),
                  VNOW - timedelta(days=100))

    # candidates + applications
    for i, cid in enumerate(CAND):
        c_created = VNOW - timedelta(days=10 + i * 2)
        cdata = _dto.candidate_dto(
            entity_id=str(cid), name=f"Candidate {i}", email=f"cand{i}@example.com",
            phone=f"+1555000000{i}", created_at=c_created, updated_at=c_created,
            application_ids=[str(APP[i])], social_links=[
                {"type": "LinkedIn", "url": f"https://linkedin.com/in/cand{i}"}],
            tags=[], position="Engineer", company="Stripe", school="MIT",
            profile_url=f"https://app.ashbyhq.com/candidates/{cid}",
            source={"id": str(uuid4()), "title": "LinkedIn", "isArchived": False,
                    "sourceType": "JobPost"})
        await ins("candidate", cid, None, cdata, c_created, c_created)

    for (aid, status, days) in APP_SPEC:
        a_created = VNOW - timedelta(days=days + 1)
        a_updated = VNOW - timedelta(days=days)
        stage = {"id": str(uuid4()), "title": "Technical Screen", "type": "Active",
                 "orderInInterviewPlan": 2, "interviewStageGroupId": str(uuid4()),
                 "interviewPlanId": str(uuid4())}
        cand_ref = {"id": str(CAND[0]), "name": "Candidate 0",
                    "primaryEmailAddress": {"value": "cand0@example.com", "type": "Work",
                                            "isPrimary": True},
                    "primaryPhoneNumber": {"value": "+15550000000", "type": "Mobile",
                                           "isPrimary": True}}
        job_ref = {"id": str(JOB[0]), "title": "Senior Backend Engineer",
                   "locationId": str(uuid4()), "departmentId": str(uuid4())}
        data = _dto.application_dto(
            entity_id=str(aid), created_at=a_created, updated_at=a_updated, status=status,
            candidate_ref=cand_ref, current_stage=stage, job_ref=job_ref,
            source={"id": str(uuid4()), "title": "Referral", "isArchived": False,
                    "sourceType": "Referral"},
            hiring_team=[],
            archived_at=(a_updated if status == "Archived" else None))
        await ins("application", aid, status, data, a_created, a_updated)

    # offers
    for i, oid in enumerate(OFF):
        created = VNOW - timedelta(days=5 - i)
        accept = ["Accepted", "WaitingOnResponse"][i]
        ostatus = ["CandidateAccepted", "WaitingOnCandidateResponse"][i]
        data = _dto.offer_dto(
            entity_id=str(oid), application_id=str(APP[2]), acceptance_status=accept,
            offer_status=ostatus, decided_at=(created if i == 0 else None),
            version_created_at=created, start_date="2026-03-01",
            salary={"value": 200000, "currencyCode": "USD"})
        await ins("offer", oid, accept, data, created, created)

    return run_id


@pytest_asyncio.fixture(loop_scope="session")
async def ashby_client(pool, ashby_run):
    from spammers.ashby import state as a_state
    from spammers.ashby.app import create_app, _FORCED_429

    a_state._STATE = a_state.AshbyMockState(pool=pool, run_id=ashby_run)
    _FORCED_429["count"] = 0
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://mock") as c:
        yield c
    a_state._STATE = None


@pytest.fixture
def ashby_auth() -> dict[str, str]:
    return basic_header()
