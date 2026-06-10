"""Realistic Gusto corpus seeding.

Gusto is a NET-NEW Tier-C source: the frozen run has no Gusto corpus, so we model
realistic content ourselves (the brief sanctions this, like ramp projecting QB
purchases onto a card stream). Gusto is the company's **payroll + HR** system of
record, so we project the run's existing people onto:

  * **EMPLOYEES** — one Gusto employee per ``org.people`` row, with a deterministic
    annual compensation (by role/seniority), department, hire date (from
    ``started_at``), termination (from ``ended_at``), and a ``version`` token.
  * **PAYROLLS** — a **bi-weekly** (every 14 days) regular payroll run stream over
    ~18 months ending at the run's ``virtual_now``. Each run's totals (gross/net/
    taxes) are the sum across the active workforce. This is the primary stream
    (>25/year → a genuine multi-page walk; >1yr span → a year-window walk).

Money is stored as integer CENTS; the wire projects a decimal STRING in dollars.
Everything is deterministic off the run seed. Idempotent: a second call after the
company row exists is a no-op.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from random import Random
from typing import Optional
from uuid import UUID, uuid4

import asyncpg

# Seed-stable company identity (hand these to the ingest-client / memory).
COMPANY_NAME = "Alpen Labs Inc."
TRADE_NAME = "Alpen Labs"
COMPANY_UUID = "a1b2c3d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
EIN = "88-3917465"
PAY_SCHEDULE_UUID = "f0e1d2c3-b4a5-4968-8778-695a4b3c2d1e"
CLIENT_ID = "gusto_app_5f3a9c2e7b1d4068"
CLIENT_SECRET = "gusto_secret_aGmZ4kP9xWqT2sLcV7nR8yB3dF6hJ1kM0pQ"
ACCESS_TOKEN = "PF9RH-QVnURJAY9-gustoMockToken00C71HOPq7rC"
REFRESH_TOKEN = "RF2Kp-MWvTRJBZ8-gustoMockRefresh00X1HOPq7rD"
WEBHOOK_SECRET = "gwhv_3f9a2c8e1b7d4506a9c2e8f1b7d45063a8c1"

# role -> (base annual USD, payment_unit). Deterministic compensation by seniority.
_ROLE_BASE = {
    "founder": (240_000, "Year"),
    "exec": (220_000, "Year"),
    "lead": (195_000, "Year"),
    "manager": (185_000, "Year"),
    "senior": (175_000, "Year"),
    "engineer": (155_000, "Year"),
    "researcher": (165_000, "Year"),
    "designer": (145_000, "Year"),
    "analyst": (125_000, "Year"),
    "ops": (110_000, "Year"),
    "intern": (52, "Hour"),
}
_DEPARTMENTS = ["Engineering", "Research", "Operations", "Design", "Go-To-Market"]


def _rate_for(role: Optional[str], rng: Random) -> tuple[int, str]:
    key = (role or "engineer").lower()
    base, unit = next((v for k, v in _ROLE_BASE.items() if k in key), (150_000, "Year"))
    jitter = rng.randint(-8_000, 12_000) if unit == "Year" else rng.randint(-6, 10)
    return (base + jitter) * 100, unit  # cents


async def seed_gusto(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    at: Optional[datetime] = None,
) -> dict[str, int]:
    """Provision the company + employees + bi-weekly payroll stream.

    Idempotent. Returns ``{"employees": E, "payrolls": P}``."""
    existing = await pool.fetchval(
        "SELECT id FROM app_gusto.companies WHERE run_id = $1", run_id)
    if existing is not None:
        return {"employees": 0, "payrolls": 0}

    seed_row = await pool.fetchrow(
        "SELECT seed, virtual_now FROM org.runs WHERE id = $1", run_id)
    if seed_row is None:
        raise LookupError(f"no run {run_id}")
    rng = Random(int(seed_row["seed"]) ^ 0x67_75_73_74)  # 'gust'
    now = at or seed_row["virtual_now"] or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    company_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_gusto.companies
            (id, run_id, base_url, company_uuid, name, trade_name, ein, entity_type,
             company_status, tier, join_date, pay_schedule_uuid, client_id,
             client_secret, access_token, refresh_token, webhook_secret, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,'C-Corporation','Approved','complete',$8,$9,
                   $10,$11,$12,$13,$14,$15)""",
        company_pk, run_id, "https://api.gusto.com", COMPANY_UUID, COMPANY_NAME,
        TRADE_NAME, EIN, (now - timedelta(days=1100)).date(), PAY_SCHEDULE_UUID,
        CLIENT_ID, CLIENT_SECRET, ACCESS_TOKEN, REFRESH_TOKEN, WEBHOOK_SECRET,
        now - timedelta(days=1100))

    # ---- EMPLOYEES: one per org.people row -----------------------------------
    people = await pool.fetch(
        "SELECT handle, full_name, email, role, started_at, ended_at FROM org.people "
        "WHERE run_id = $1 ORDER BY started_at, handle", run_id)
    active_emps: list[tuple] = []  # (employee_uuid, rate_cents)
    emp_count = 0
    for i, p in enumerate(people):
        euid = str(UUID(int=rng.getrandbits(128), version=4))
        full = (p["full_name"] or p["handle"] or "Employee").strip()
        first, _, last = full.partition(" ")
        rate_cents, unit = _rate_for(p["role"], rng)
        started = p["started_at"] or (now - timedelta(days=500))
        if isinstance(started, datetime):
            started = started.date() if started else None
        ended = p["ended_at"]
        if isinstance(ended, datetime):
            ended = ended.date()
        terminated = ended is not None and ended <= now.date()
        version = "%032x" % rng.getrandbits(128)
        await pool.execute(
            """INSERT INTO app_gusto.employees
                (id, company_pk, employee_uuid, first_name, last_name, email, work_email,
                 department, employee_code, current_employment_status, onboarding_status,
                 terminated, onboarded, hire_date, termination_date, date_of_birth,
                 job_uuid, job_title, rate_cents, payment_unit, flsa_status, version,
                 sort_key, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'onboarding_completed',$11,TRUE,
                       $12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22)""",
            uuid4(), company_pk, euid, first, last or "",
            p["email"], p["email"], rng.choice(_DEPARTMENTS), f"E{1000 + i}",
            "part_time" if unit == "Hour" else "full_time", terminated,
            started, ended if terminated else None,
            date(1970 + (i % 30), 1 + (i % 12), 1 + (i % 27)),
            str(UUID(int=rng.getrandbits(128), version=4)),
            (p["role"] or "Engineer").title(), rate_cents, unit,
            "Nonexempt" if unit == "Hour" else "Exempt", version, i, now - timedelta(days=900))
        emp_count += 1
        if not terminated:
            # annualise hourly for the payroll-total approximation (2080 hrs/yr)
            annual = rate_cents * 2080 if unit == "Hour" else rate_cents
            active_emps.append((euid, annual))

    # ---- PAYROLLS: bi-weekly regular runs over ~18 months --------------------
    period_gross = sum(c // 24 for _e, c in active_emps)  # one period (semi-monthly basis)
    payroll_count = 0
    n_periods = 39  # ~18 months bi-weekly
    for k in range(n_periods):
        check = now.date() - timedelta(days=14 * k)
        pp_end = check - timedelta(days=3)
        pp_start = pp_end - timedelta(days=13)
        gross = period_gross
        employee_taxes = int(gross * 0.18)
        employer_taxes = int(gross * 0.0765)
        benefits = int(gross * 0.04)
        net = gross - employee_taxes - benefits
        calc = datetime(check.year, check.month, check.day, tzinfo=timezone.utc) - timedelta(days=2)
        await pool.execute(
            """INSERT INTO app_gusto.payrolls
                (id, company_pk, payroll_uuid, pay_period_start, pay_period_end,
                 check_date, pay_schedule_uuid, processed, off_cycle, external,
                 payroll_type, processed_at, calculated_at, payroll_deadline,
                 gross_pay_cents, net_pay_cents, employer_taxes_cents,
                 employee_taxes_cents, benefits_cents, reimbursements_cents,
                 sort_key, is_historical)
               VALUES ($1,$2,$3,$4,$5,$6,$7,TRUE,FALSE,FALSE,'regular',$8,$9,$10,
                       $11,$12,$13,$14,$15,0,$16,TRUE)""",
            uuid4(), company_pk, str(UUID(int=rng.getrandbits(128), version=4)),
            pp_start, pp_end, check, PAY_SCHEDULE_UUID,
            calc + timedelta(days=1), calc, calc - timedelta(days=2),
            gross, net, employer_taxes, employee_taxes, benefits,
            n_periods - k)  # sort_key ascending with check_date (oldest = smallest)
        payroll_count += 1

    return {"employees": emp_count, "payrolls": payroll_count}
