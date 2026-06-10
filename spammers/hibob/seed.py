"""Realistic HiBob corpus seeding.

HiBob is a NET-NEW Tier-C source: the frozen run has no HiBob corpus, so we model
realistic content ourselves (the brief sanctions this, like ashby projecting the
org chart and deel projecting contracts/invoices). HiBob is the company's **HR
system of record**, so we project the run's people into HiBob:

  * one **EMPLOYEE** per person (the People directory — ``POST /v1/people/search``;
    active vs inactive by whether they have left);
  * a **SALARY** history per active employee (the ``/v1/bulk/people/salaries``
    payroll stream — one current salary + a prior raise entry for some, enough to
    cross the default page size so the CURSOR walk is exercised);
  * a **TIME-OFF CHANGE** stream over the recent window (the
    ``/v1/timeoff/requests/changes`` feed — holiday / sick / personal requests
    within the last ~6 months so a single ``since``/``to`` window picks them up).

Salary money is stored as integer cents internally and rendered as a plain NUMBER
in major units (``base:{value, currency}``). Everything is deterministic off the
run seed. Idempotent: a second call after the company row exists is a no-op.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from random import Random
from typing import Optional
from uuid import UUID, uuid4

import asyncpg

# Seed-stable identity (hand these to the ingest-client / memory).
LEGAL_BUSINESS_NAME = "Alpen Labs Inc."
COMPANY_ID = "636192"                       # webhook companyId (numeric string)
SERVICE_USER_ID = "alpenlabs-svc-ingest"    # Basic username (public half)
SERVICE_USER_TOKEN = "Qe8q89RwbzeS7mmhMcAsN1crM73m6MdbjewGCCUY"  # Basic password (secret half)
WEBHOOK_SECRET = "bobhk_4f1c9e7a52b84d06c1a9f4e2d7b605c8e3a0f1d6b9c2e5a8f4d7b0c3e6a9f2d5"

_POLICIES = ["Holiday", "Sick", "Personal", "Work From Home"]


def _numeric_id(rng: Random, digits: int = 19) -> str:
    """A HiBob-style opaque numeric-string id (no leading zero)."""
    first = rng.randint(1, 9)
    return str(first) + "".join(str(rng.randint(0, 9)) for _ in range(digits - 1))


def _is_manager(role: str, level: str) -> bool:
    blob = f"{role} {level}".lower()
    return any(w in blob for w in ("manager", "lead", "head", "director", "vp",
                                   "principal", "chief", "founder"))


async def seed_hibob(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    at: Optional[datetime] = None,
) -> dict[str, int]:
    """Provision the company + one employee per person + salary + time-off streams.

    Idempotent. Returns ``{"employees": E, "salaries": S, "timeoff": T}``."""
    existing = await pool.fetchval(
        "SELECT id FROM app_hibob.companies WHERE run_id = $1", run_id)
    if existing is not None:
        return {"employees": 0, "salaries": 0, "timeoff": 0}

    seed_row = await pool.fetchrow(
        "SELECT seed, virtual_now FROM org.runs WHERE id = $1", run_id)
    if seed_row is None:
        raise LookupError(f"no run {run_id}")
    rng = Random(int(seed_row["seed"]) ^ 0x6869_626F)  # 'hibo'
    now = at or seed_row["virtual_now"] or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    co_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_hibob.companies
            (id, run_id, base_url, legal_business_name, company_id, service_user_id,
             service_user_token, webhook_secret, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
        co_pk, run_id, "https://api.hibob.com", LEGAL_BUSINESS_NAME, COMPANY_ID,
        SERVICE_USER_ID, SERVICE_USER_TOKEN, WEBHOOK_SECRET, now - timedelta(days=1200))

    people = await pool.fetch(
        "SELECT full_name, email, role, level, started_at, ended_at "
        "FROM org.people WHERE run_id = $1 ORDER BY started_at, email", run_id)

    employees = salaries = timeoff = 0
    salary_seq = 100_000
    request_seq = 5_000_000
    window_floor = now - timedelta(days=170)   # keep time-off inside one 6-month window
    for idx, p in enumerate(people):
        started = p["started_at"]
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        ended = p["ended_at"]
        if ended is not None and ended.tzinfo is None:
            ended = ended.replace(tzinfo=timezone.utc)
        active = ended is None or ended > now

        emp_id = _numeric_id(rng)
        first, _, surname = p["full_name"].partition(" ")
        surname = surname or "—"
        modified = (ended if (ended is not None and ended <= now)
                    else now - timedelta(days=rng.randint(1, 120)))
        await pool.execute(
            """INSERT INTO app_hibob.employees
                (id, company_pk, employee_id, first_name, surname, second_name, full_name,
                 display_name, email, avatar_url, work_title, work_department, work_site,
                 work_manager_name, work_reports_to_id, work_start_date, work_is_manager,
                 work_employee_id_in_company, about_text, creation_date_time, modified,
                 is_active, sort_key, is_historical)
               VALUES ($1,$2,$3,$4,$5,'',$6,$6,$7,$8,$9,$10,$11,NULL,NULL,$12,$13,$14,
                       '',$15,$16,$17,$18,TRUE)""",
            uuid4(), co_pk, emp_id, first or p["full_name"], surname, p["full_name"],
            p["email"], f"https://avatars.hibob.com/{emp_id}.png", p["role"],
            (p["level"] or "Team"), rng.choice(["HQ", "Remote", "London", "Berlin"]),
            started.date(), _is_manager(p["role"], p["level"] or ""), str(idx + 1),
            started, modified, active, idx)
        employees += 1

        # --- salary history (current + an optional prior raise) -----------------
        annual = rng.randint(80, 220) * 1000
        salary_seq += 1
        await pool.execute(
            """INSERT INTO app_hibob.salaries
                (id, company_pk, salary_id, employee_id, base_value_cents, currency,
                 pay_period, pay_frequency, effective_date, is_current, creation_date,
                 modification_date, sort_key, is_historical)
               VALUES ($1,$2,$3,$4,$5,'USD','Annual','Monthly',$6,TRUE,$7,$7,$8,TRUE)""",
            uuid4(), co_pk, salary_seq, emp_id, annual * 100,
            max(started, now - timedelta(days=365)).date(),
            max(started, now - timedelta(days=365)), salary_seq)
        salaries += 1
        if active and rng.random() < 0.5:
            prior = int(annual * rng.uniform(0.82, 0.93)) * 100
            salary_seq += 1
            eff = max(started, now - timedelta(days=730))
            await pool.execute(
                """INSERT INTO app_hibob.salaries
                    (id, company_pk, salary_id, employee_id, base_value_cents, currency,
                     pay_period, pay_frequency, effective_date, is_current, creation_date,
                     modification_date, sort_key, is_historical)
                   VALUES ($1,$2,$3,$4,$5,'USD','Annual','Monthly',$6,FALSE,$7,$7,$8,TRUE)""",
                uuid4(), co_pk, salary_seq, emp_id, prior, eff.date(), eff, salary_seq)
            salaries += 1

        # --- time-off changes in the recent window (active employees) ----------
        if active:
            for _ in range(rng.randint(0, 3)):
                request_seq += 1
                created_on = window_floor + timedelta(
                    days=rng.randint(0, 165), hours=rng.randint(8, 17))
                leave_start = (created_on + timedelta(days=rng.randint(5, 30))).date()
                dur = rng.randint(1, 5)
                roll = rng.random()
                if roll < 0.78:
                    change_type, status = "Created", "approved"
                elif roll < 0.9:
                    change_type, status = "Pending", "pending"
                else:
                    change_type, status = "Canceled", "cancelled"
                await pool.execute(
                    """INSERT INTO app_hibob.timeoff_changes
                        (id, company_pk, request_id, employee_id, employee_display_name,
                         employee_email, policy_type_display_name, change_type, status,
                         created_on, start_date, end_date, duration_unit, total_duration,
                         total_cost, request_type, sort_key, is_historical)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,'days',$13,$13,
                               'days',$14,TRUE)""",
                    uuid4(), co_pk, request_seq, emp_id, p["full_name"], p["email"],
                    rng.choice(_POLICIES), change_type, status, created_on, leave_start,
                    leave_start + timedelta(days=dur - 1), dur,
                    int(created_on.timestamp()))
                timeoff += 1

    return {"employees": employees, "salaries": salaries, "timeoff": timeoff}
