"""Realistic Ashby corpus seeding.

Ashby is a NET-NEW Tier-C source: the frozen run has no Ashby corpus, so we model
realistic recruiting content ourselves (the brief sanctions this, like grafana
synthesising annotations and mercury projecting a bank stream). The honest move
for an ATS is to derive the hiring funnel from the company the run already
describes — a real startup recruits INTO the teams it already has. So we project:

  * the run's ``org.teams`` -> recruiting **departments**, each opening a couple of
    **jobs** (titles drawn from the team's discipline);
  * a deterministic set of external **candidates** who **apply** to those jobs, with
    a realistic status mix (mostly Active, some Hired/Archived, a few Leads);
  * an **interview** plan (interview-type definitions) per discipline;
  * **offers** for the hired applications (+ a few pending).

The run's ``org.people`` become the hiring team / interviewers on jobs and
applications. Everything is deterministic off the run seed; idempotent (a second
call after the org row exists is a no-op). If the run has no teams/people a small
synthetic catalogue is used.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from random import Random
from typing import Optional
from uuid import UUID

import asyncpg

from spammers.ashby import dto as _dto

# Seed-stable org identity (hand these to the ingest-client / memory).
LEGAL_BUSINESS_NAME = "Alpen Labs Inc."
ORG_ID = "alpenlabs"
API_KEY = "ashby_live_3xN8pQ2kR7wT5vY9mB4cF6hJ1sD0aL"
WEBHOOK_SECRET = "5e2c9a47f0b318d6e4c70a92f5b81d3c6e0a94f72b5d8c1e6f3a0b9d7c4e8f1a2"

# Discipline -> job-title catalogue (picked by the team name's keywords).
_TITLES = {
    "engineering": ["Senior Software Engineer", "Staff Engineer", "Backend Engineer",
                    "Engineering Manager"],
    "infrastructure": ["Site Reliability Engineer", "Platform Engineer",
                       "Infrastructure Engineer"],
    "research": ["Research Scientist", "Research Engineer", "Cryptography Researcher"],
    "product": ["Product Manager", "Senior Product Manager"],
    "design": ["Product Designer", "Brand Designer"],
    "devrel": ["Developer Advocate", "Developer Relations Engineer"],
    "operations": ["Operations Manager", "People Operations Lead", "Recruiter"],
    "bridge": ["Protocol Engineer", "Smart Contract Engineer"],
    "_default": ["Software Engineer", "Senior Software Engineer", "Operations Associate"],
}
_LOCATIONS = ["San Francisco, CA", "Remote — US", "New York, NY"]
_EMPLOYMENT = ["FullTime", "FullTime", "FullTime", "Contract", "Intern"]

_FIRST = ["Ava", "Liam", "Noah", "Emma", "Oliver", "Sophia", "Mateo", "Isabella",
          "Lucas", "Mia", "Ethan", "Amara", "Kenji", "Priya", "Diego", "Yuki",
          "Omar", "Chloe", "Arjun", "Fatima", "Leo", "Nina", "Hassan", "Grace",
          "Tomas", "Aisha", "Daniel", "Wei", "Sofia", "Andre", "Maya", "Ivan",
          "Zoe", "Raj", "Elena", "Kofi", "Hana", "Pablo", "Sara", "Nikhil"]
_LAST = ["Patel", "Nguyen", "Kim", "Garcia", "Smith", "Okafor", "Johnson", "Lee",
         "Martinez", "Chen", "Brown", "Singh", "Rossi", "Tanaka", "Mueller",
         "Silva", "Ahmed", "Williams", "Lopez", "Park", "Kowalski", "Haddad",
         "Andersson", "Romano", "Dubois", "Ferreira", "Novak", "Reyes", "Ito",
         "Walsh"]
_SOURCES = [
    ("LinkedIn", "JobPost"), ("Referral", "Referral"), ("Sourced (LinkedIn)", "Sourced"),
    ("Company Website", "JobPost"), ("Indeed", "JobPost"), ("Agency", "Agency"),
]
_STAGES = [
    ("Application Review", "PreInterviewScreen", 0),
    ("Recruiter Screen", "Active", 1),
    ("Technical Screen", "Active", 2),
    ("Onsite", "Active", 3),
    ("Offer", "Offer", 4),
]
_HIRED_STAGE = ("Hired", "Hired", 5)
_ARCHIVED_STAGE = ("Rejected", "Archived", 6)
_INTERVIEW_TYPES = [
    "Recruiter Phone Screen", "Hiring Manager Screen", "Technical Phone Screen",
    "System Design Interview", "Coding Interview", "Values & Culture Interview",
    "Onsite Debrief", "Executive Interview",
]

# How many candidates to synthesise — sized to cross the limit=100 page boundary so
# the cursor walk genuinely paginates.
_N_CANDIDATES = 130


def _det_uuid(rng: Random) -> UUID:
    return UUID(int=rng.getrandbits(128))


def _disc_titles(team_name: str) -> list[str]:
    low = team_name.lower()
    for key, titles in _TITLES.items():
        if key != "_default" and key in low:
            return titles
    # keyword heuristics for the run's actual team names
    if "engineer" in low or "protocol" in low:
        return _TITLES["engineering"]
    return _TITLES["_default"]


async def _people(pool: asyncpg.Pool, run_id: UUID) -> list[dict]:
    try:
        rows = await pool.fetch(
            "SELECT full_name, email, role FROM org.people WHERE run_id = $1 "
            "ORDER BY handle", run_id)
        return [dict(r) for r in rows]
    except asyncpg.PostgresError:
        return []


async def _teams(pool: asyncpg.Pool, run_id: UUID) -> list[str]:
    try:
        rows = await pool.fetch(
            "SELECT name FROM org.teams WHERE run_id = $1 ORDER BY name", run_id)
        return [r["name"] for r in rows]
    except asyncpg.PostgresError:
        return []


def _hiring_member(person: dict, rng: Random, role: str) -> dict:
    name = (person.get("full_name") or "Alex Doe").split()
    first = name[0]
    last = name[-1] if len(name) > 1 else ""
    return {
        "email": person.get("email") or f"{first.lower()}@alpenlabs.io",
        "firstName": first,
        "lastName": last,
        "role": role,
        "userId": str(_det_uuid(rng)),
    }


async def seed_ashby(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    at: Optional[datetime] = None,
) -> dict[str, int]:
    """Provision the org + a realistic recruiting entity stream for ``run_id``.

    Idempotent. Returns per-kind counts (zeros if already seeded)."""
    existing = await pool.fetchval(
        "SELECT id FROM app_ashby.organizations WHERE run_id = $1", run_id)
    if existing is not None:
        return {k: 0 for k in _dto.CATEGORIES}

    seed_row = await pool.fetchrow(
        "SELECT seed, virtual_now FROM org.runs WHERE id = $1", run_id)
    if seed_row is None:
        raise LookupError(f"no run {run_id}")
    rng = Random(int(seed_row["seed"]) ^ 0x6173_6862)  # 'ashb'
    now = at or seed_row["virtual_now"] or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    org_pk = _det_uuid(rng)
    await pool.execute(
        """INSERT INTO app_ashby.organizations
            (id, run_id, base_url, org_id, legal_business_name, api_key, webhook_secret,
             created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
        org_pk, run_id, "https://api.ashbyhq.com", ORG_ID, LEGAL_BUSINESS_NAME,
        API_KEY, WEBHOOK_SECRET, now - timedelta(days=900))

    people = await _people(pool, run_id)
    team_names = await _teams(pool, run_id) or ["Engineering", "Operations"]
    counts = {k: 0 for k in _dto.CATEGORIES}

    async def _insert(kind: str, ent_id: UUID, data: dict, status: Optional[str],
                      created: datetime, updated: datetime) -> None:
        await pool.execute(
            """INSERT INTO app_ashby.entities
                (id, org_pk, kind, entity_id, status, data, created_at, updated_at,
                 is_historical)
               VALUES ($1,$2,$3,$1,$4,$5::jsonb,$6,$7,TRUE)""",
            ent_id, org_pk, kind, status, json.dumps(data), created, updated)
        counts[kind] += 1

    # ---- departments + locations (ids only; not separately listed) ----
    dept_ids = {name: str(_det_uuid(rng)) for name in team_names}
    loc_ids = [str(_det_uuid(rng)) for _ in _LOCATIONS]

    # ---- jobs (a couple per team) + interview plan per job ----
    jobs: list[dict] = []
    for team in team_names:
        titles = _disc_titles(team)
        n_jobs = rng.randint(1, min(3, len(titles)))
        for title in rng.sample(titles, n_jobs):
            jid = _det_uuid(rng)
            opened = now - timedelta(days=rng.randint(20, 400))
            # ~20% of jobs are closed/filled.
            roll = rng.random()
            if roll < 0.6:
                jstatus, closed, jupdated = "Open", None, now - timedelta(days=rng.randint(0, 20))
            elif roll < 0.8:
                closed = opened + timedelta(days=rng.randint(30, 120))
                jstatus, jupdated = "Closed", closed
            else:
                jstatus, closed, jupdated = "Draft", None, opened
            plan_id = str(_det_uuid(rng))
            loc_idx = rng.randrange(len(loc_ids))
            hire_team = ([_hiring_member(rng.choice(people), rng, "HiringManager"),
                          _hiring_member(rng.choice(people), rng, "Recruiter")]
                         if people else [])
            data = _dto.job_dto(
                entity_id=str(jid), title=title, status=jstatus,
                employment_type=rng.choice(_EMPLOYMENT), location_id=loc_ids[loc_idx],
                department_id=dept_ids[team], default_interview_plan_id=plan_id,
                interview_plan_ids=[plan_id], job_posting_ids=[str(_det_uuid(rng))],
                hiring_team=hire_team, created_at=opened, updated_at=jupdated,
                opened_at=(opened if jstatus != "Draft" else None), closed_at=closed,
                confidential=(rng.random() < 0.15))
            await _insert("job", jid, data, jstatus, opened, jupdated)
            jobs.append({"id": str(jid), "title": title, "loc": loc_ids[loc_idx],
                         "dept": dept_ids[team], "plan": plan_id, "team": team,
                         "status": jstatus, "opened": opened})

    # ---- interview-type definitions (a handful, attached to a random open job) ----
    open_jobs = [j for j in jobs if j["status"] == "Open"] or jobs
    for title in _INTERVIEW_TYPES:
        iid = _det_uuid(rng)
        host = rng.choice(open_jobs)
        data = _dto.interview_dto(
            entity_id=str(iid), title=title, job_id=host["id"],
            feedback_form_definition_id=str(_det_uuid(rng)),
            is_feedback_required=(title != "Onsite Debrief"),
            is_debrief=(title == "Onsite Debrief"),
            instructions_plain=f"{title} — evaluate against the role rubric.")
        # interview definitions carry no wire timestamps; use the host job's dates
        # as the internal ordering keys.
        await _insert("interview", iid, data, None, host["opened"],
                      host["opened"] + timedelta(days=1))

    # ---- candidates + applications + offers ----
    offers_pending_pool: list[dict] = []
    for n in range(_N_CANDIDATES):
        first, last = rng.choice(_FIRST), rng.choice(_LAST)
        name = f"{first} {last}"
        cid = _det_uuid(rng)
        email = f"{first.lower()}.{last.lower()}{n}@example.com"
        phone = f"+1{rng.randint(2000000000, 9999999999)}"
        c_created = now - timedelta(days=rng.randint(1, 700),
                                    hours=rng.randint(0, 23))
        # 1 application for most candidates, a second for ~20%.
        n_apps = 2 if rng.random() < 0.2 else 1
        app_ids: list[str] = []
        c_updated = c_created
        for _ in range(n_apps):
            job = rng.choice(jobs)
            aid = _det_uuid(rng)
            a_created = c_created + timedelta(minutes=rng.randint(0, 600))
            roll = rng.random()
            if roll < 0.58:
                status = "Active"
                stage = rng.choice(_STAGES)
                a_updated = a_created + timedelta(days=rng.randint(0, 40))
                archived_at = archive_reason = None
            elif roll < 0.74:
                status = "Hired"
                stage = _HIRED_STAGE
                a_updated = a_created + timedelta(days=rng.randint(20, 90))
                archived_at = archive_reason = None
            elif roll < 0.94:
                status = "Archived"
                stage = _ARCHIVED_STAGE
                a_updated = a_created + timedelta(days=rng.randint(3, 60))
                archived_at = a_updated
                archive_reason = {"id": str(_det_uuid(rng)),
                                  "text": rng.choice(["Not enough experience",
                                                      "Position filled",
                                                      "Candidate withdrew",
                                                      "Stronger candidates"]),
                                  "reasonType": rng.choice(["RejectedByOrg",
                                                            "RejectedByCandidate",
                                                            "Other"]),
                                  "isArchived": True, "customFields": []}
            else:
                status = "Lead"
                stage = _STAGES[0]
                a_updated = a_created
                archived_at = archive_reason = None

            src_title, src_type = rng.choice(_SOURCES)
            source = {"id": str(_det_uuid(rng)), "title": src_title,
                      "isArchived": False, "sourceType": src_type}
            cand_ref = {"id": str(cid), "name": name,
                        "primaryEmailAddress": {"value": email, "type": "Work",
                                                "isPrimary": True},
                        "primaryPhoneNumber": {"value": phone, "type": "Mobile",
                                               "isPrimary": True}}
            job_ref = {"id": job["id"], "title": job["title"],
                       "locationId": job["loc"], "departmentId": job["dept"]}
            stage_obj = {"id": str(_det_uuid(rng)), "title": stage[0],
                         "type": stage[1], "orderInInterviewPlan": stage[2],
                         "interviewStageGroupId": str(_det_uuid(rng)),
                         "interviewPlanId": job["plan"]}
            hire_team = ([_hiring_member(rng.choice(people), rng, "Recruiter")]
                         if people else [])
            data = _dto.application_dto(
                entity_id=str(aid), created_at=a_created, updated_at=a_updated,
                status=status, candidate_ref=cand_ref, current_stage=stage_obj,
                job_ref=job_ref, source=source, hiring_team=hire_team,
                archive_reason=archive_reason, archived_at=archived_at,
                applied_via_job_posting_id=(job.get("posting")))
            await _insert("application", aid, data, status, a_created, a_updated)
            app_ids.append(str(aid))
            c_updated = max(c_updated, a_updated)

            # Offers: every Hired app + a few Active-at-Offer-stage apps.
            if status == "Hired":
                offers_pending_pool.append({"app": str(aid), "accept": "Accepted",
                                            "ostatus": "CandidateAccepted",
                                            "decided": a_updated, "created": a_updated,
                                            "job": job})
            elif status == "Active" and stage[0] == "Offer" and rng.random() < 0.7:
                offers_pending_pool.append({"app": str(aid), "accept": "WaitingOnResponse",
                                            "ostatus": "WaitingOnCandidateResponse",
                                            "decided": None, "created": a_updated,
                                            "job": job})

        # candidate social links + tags + source
        socials = [{"type": "LinkedIn",
                    "url": f"https://linkedin.com/in/{first.lower()}-{last.lower()}"}]
        if rng.random() < 0.4:
            socials.append({"type": "GitHub",
                            "url": f"https://github.com/{first.lower()}{last.lower()}"})
        tags = ([{"id": str(_det_uuid(rng)), "title": rng.choice(
            ["Top Prospect", "Referral", "Diversity", "Senior", "Passive"]),
            "isArchived": False}] if rng.random() < 0.5 else [])
        c_src_title, c_src_type = rng.choice(_SOURCES)
        c_source = {"id": str(_det_uuid(rng)), "title": c_src_title,
                    "isArchived": False, "sourceType": c_src_type}
        cdata = _dto.candidate_dto(
            entity_id=str(cid), name=name, email=email, phone=phone,
            created_at=c_created, updated_at=c_updated, application_ids=app_ids,
            social_links=socials, tags=tags,
            position=rng.choice(["Software Engineer", "Senior Engineer",
                                 "Researcher", "Designer", "Product Manager", None]),
            company=rng.choice(["Stripe", "Google", "Coinbase", "Independent",
                                "Anthropic", None]),
            school=rng.choice(["MIT", "Stanford", "UC Berkeley", "CMU",
                               "Waterloo", None]),
            profile_url=f"https://app.ashbyhq.com/candidates/{cid}",
            source=c_source,
            timezone_name=rng.choice(["America/New_York", "America/Los_Angeles",
                                      "Europe/London", "Asia/Singapore"]))
        await _insert("candidate", cid, cdata, None, c_created, c_updated)

    # ---- offers ----
    for o in offers_pending_pool:
        oid = _det_uuid(rng)
        start = (o["created"] + timedelta(days=rng.randint(14, 45))).date().isoformat()
        salary = {"value": rng.randrange(140000, 260000, 5000), "currencyCode": "USD"}
        data = _dto.offer_dto(
            entity_id=str(oid), application_id=o["app"], acceptance_status=o["accept"],
            offer_status=o["ostatus"], decided_at=o["decided"],
            version_created_at=o["created"], start_date=start, salary=salary,
            opening_id=str(_det_uuid(rng)))
        await _insert("offer", oid, data, o["accept"], o["created"],
                      o["decided"] or o["created"])

    return counts
