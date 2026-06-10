"""Fixtures for the Gusto mock fidelity suite (employees/payrolls + the OAuth
token mint + the X-Gusto-Signature webhook).

Seeds a deterministic company with:
  * three EMPLOYEES (one terminated) — enough to walk a 2-page offset list at per=2;
  * seven PAYROLLS: five within the default 6-month window + two older than a year
    (so the default-window + ≤1-year-span + year-window-walk behaviours are testable).

Wires the Gusto ``state`` singleton + an ASGI client.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

ACCESS_TOKEN = "PF9RH-QVnURJAY9-fidelityMockToken00HOPq7rC"
REFRESH_TOKEN = "RF2Kp-MWvTRJBZ8-fidelityMockRefresh0HOPq7rD"
WEBHOOK_SECRET = "gwhv_fidelity00000000000000000000000000"
CLIENT_ID = "gusto_app_fidelity0001"
CLIENT_SECRET = "gusto_secret_fidelity0001"
COMPANY_UUID = "a1b2c3d4-5e6f-4a7b-8c9d-000000000001"
COMPANY_NAME = "Alpen Labs Inc."

VNOW = datetime(2026, 2, 1, tzinfo=timezone.utc)

EMP_A = "11111111-1111-4111-8111-111111111111"
EMP_B = "22222222-2222-4222-8222-222222222222"
EMP_C = "33333333-3333-4333-8333-333333333333"

# (employee_uuid, first, last, rate_cents, terminated)
EMPLOYEES = [
    (EMP_A, "Alice", "Anderson", 220_000_00, False),
    (EMP_B, "Bob", "Brown", 155_000_00, False),
    (EMP_C, "Carol", "Clark", 145_000_00, True),
]

# (payroll_uuid, check_date, pay_period_start, pay_period_end, sort_key)
PAYROLLS = [
    ("p0000002-0000-4000-8000-000000000002", date(2024, 5, 18), date(2024, 5, 2), date(2024, 5, 15), 1),
    ("p0000001-0000-4000-8000-000000000001", date(2024, 6, 1), date(2024, 5, 16), date(2024, 5, 29), 2),
    ("p5000000-0000-4000-8000-000000000005", date(2025, 12, 5), date(2025, 11, 19), date(2025, 12, 2), 3),
    ("p4000000-0000-4000-8000-000000000004", date(2025, 12, 19), date(2025, 12, 3), date(2025, 12, 16), 4),
    ("p3000000-0000-4000-8000-000000000003", date(2026, 1, 2), date(2025, 12, 17), date(2025, 12, 30), 5),
    ("p2000000-0000-4000-8000-000000000002", date(2026, 1, 16), date(2025, 12, 31), date(2026, 1, 13), 6),
    ("p1000000-0000-4000-8000-000000000001", date(2026, 1, 30), date(2026, 1, 14), date(2026, 1, 27), 7),
]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def gusto_run(pool) -> UUID:
    run_id = uuid4()
    await pool.execute(
        """INSERT INTO org.runs (id, size, runtime, seed, fyralis_tenant_id,
               fyralis_base_url, virtual_now, mode, speed_multiplier)
           VALUES ($1,'small','few_months',19,$2,'http://localhost:8000',$3,'frozen',1.0)""",
        run_id, uuid4(), VNOW)
    company_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_gusto.companies
            (id, run_id, base_url, company_uuid, name, trade_name, ein, entity_type,
             company_status, tier, join_date, pay_schedule_uuid, client_id, client_secret,
             access_token, refresh_token, webhook_secret, created_at)
           VALUES ($1,$2,'https://api.gusto.com',$3,$4,'Alpen Labs','88-3917465',
                   'C-Corporation','Approved','complete',$5,'sched-1',$6,$7,$8,$9,$10,$11)""",
        company_pk, run_id, COMPANY_UUID, COMPANY_NAME, date(2023, 1, 1),
        CLIENT_ID, CLIENT_SECRET, ACCESS_TOKEN, REFRESH_TOKEN, WEBHOOK_SECRET,
        datetime(2023, 1, 1, tzinfo=timezone.utc))

    for i, (euid, first, last, rate, term) in enumerate(EMPLOYEES):
        await pool.execute(
            """INSERT INTO app_gusto.employees
                (id, company_pk, employee_uuid, first_name, last_name, email, work_email,
                 department, employee_code, current_employment_status, onboarding_status,
                 terminated, onboarded, hire_date, termination_date, date_of_birth,
                 job_uuid, job_title, rate_cents, payment_unit, flsa_status, version,
                 sort_key, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$6,'Engineering',$7,'full_time',
                       'onboarding_completed',$8,TRUE,$9,$10,$11,$12,'Engineer',$13,'Year',
                       'Exempt',$14,$15,$16)""",
            uuid4(), company_pk, euid, first, last,
            f"{first.lower()}@alpenlabs.com", f"E{100 + i}", term,
            date(2022, 3, 1 + i), (date(2026, 1, 5) if term else None),
            date(1988, 4, 1 + i), f"job-{i}", rate, f"ver{i:032d}", i,
            datetime(2022, 3, 1, tzinfo=timezone.utc))

    for puid, check, pps, ppe, sk in PAYROLLS:
        gross = 9_500_00
        await pool.execute(
            """INSERT INTO app_gusto.payrolls
                (id, company_pk, payroll_uuid, pay_period_start, pay_period_end,
                 check_date, pay_schedule_uuid, processed, off_cycle, external,
                 payroll_type, processed_at, calculated_at, payroll_deadline,
                 gross_pay_cents, net_pay_cents, employer_taxes_cents,
                 employee_taxes_cents, benefits_cents, reimbursements_cents, sort_key)
               VALUES ($1,$2,$3,$4,$5,$6,'sched-1',TRUE,FALSE,FALSE,'regular',$7,$7,$7,
                       $8,$9,$10,$11,$12,0,$13)""",
            uuid4(), company_pk, puid, pps, ppe, check,
            datetime(check.year, check.month, check.day, tzinfo=timezone.utc),
            gross, int(gross * 0.72), int(gross * 0.0765), int(gross * 0.18),
            int(gross * 0.04), sk)
    return run_id


@pytest_asyncio.fixture(loop_scope="session")
async def gusto_client(pool, gusto_run):
    from spammers.gusto import state as g_state
    from spammers.gusto.app import create_app, _FORCED_429

    g_state._STATE = g_state.GustoMockState(pool=pool, run_id=gusto_run)
    _FORCED_429["count"] = 0
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://mock") as c:
        yield c
    g_state._STATE = None


@pytest.fixture
def gusto_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {ACCESS_TOKEN}"}
