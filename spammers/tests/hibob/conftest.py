"""Fixtures for the HiBob mock fidelity suite (people + timeoff + salaries + webhook).

Seeds a deterministic company with:
  * three EMPLOYEES (two active, one inactive) — enough to pin the
    ``{employees:[…]}`` no-pagination directory + the ``showInactive`` gate + the
    ``root.id``/``root.email`` filters;
  * a known SALARY set (5 entries: current + raises) so the suite can walk the
    CURSOR pagination at limit=2 and pin the ``{results, response_metadata}`` shape
    + the ``base:{value, currency}`` number-money;
  * a TIME-OFF CHANGE set spread across a date window so the suite can pin the
    ``since``/``to`` filter, the bare-array shape, and the >6-month-window 400.

Wires the HiBob ``state`` singleton + an ASGI client.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

SERVICE_USER_ID = "fidelity-svc-user"
SERVICE_USER_TOKEN = "fidelityToken_abc123XYZ"
WEBHOOK_SECRET = "bobhk_fidelity0000000000000000000000000000000000000000000000000000"
LEGAL_NAME = "Alpen Labs Inc."
COMPANY_ID = "990001"

VNOW = datetime(2026, 4, 1, tzinfo=timezone.utc)

# (employee_id, first, surname, email, active, created)
EMPLOYEES = [
    ("1001", "Ada", "Lovelace", "ada@example.com", True,
     datetime(2024, 6, 1, tzinfo=timezone.utc)),
    ("1002", "Alan", "Turing", "alan@example.com", True,
     datetime(2024, 7, 1, tzinfo=timezone.utc)),
    ("1003", "Grace", "Hopper", "grace@example.com", False,
     datetime(2023, 1, 1, tzinfo=timezone.utc)),
]
# (salary_id, employee_idx, base_cents, effective, is_current, sort_key)
SALARIES = [
    (501, 0, 120_000_00, date(2025, 1, 1), True, 1),
    (502, 0, 105_000_00, date(2024, 1, 1), False, 2),
    (503, 1, 140_000_00, date(2025, 6, 1), True, 3),
    (504, 1, 120_000_00, date(2024, 6, 1), False, 4),
    (505, 2, 160_000_00, date(2023, 1, 1), True, 5),
]
# (request_id, employee_idx, change_type, status, created_on, start, end)
TIMEOFF = [
    (700001, 0, "Created", "approved",
     datetime(2026, 1, 10, 9, tzinfo=timezone.utc), date(2026, 1, 20), date(2026, 1, 22)),
    (700002, 1, "Created", "approved",
     datetime(2026, 2, 14, 9, tzinfo=timezone.utc), date(2026, 3, 1), date(2026, 3, 5)),
    (700003, 0, "Canceled", "cancelled",
     datetime(2026, 3, 20, 9, tzinfo=timezone.utc), date(2026, 4, 1), date(2026, 4, 2)),
]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def hibob_run(pool) -> UUID:
    run_id = uuid4()
    await pool.execute(
        """INSERT INTO org.runs (id, size, runtime, seed, fyralis_tenant_id,
               fyralis_base_url, virtual_now, mode, speed_multiplier)
           VALUES ($1,'small','few_months',15,$2,'http://localhost:8000',$3,'frozen',1.0)""",
        run_id, uuid4(), VNOW)
    co_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_hibob.companies
            (id, run_id, base_url, legal_business_name, company_id, service_user_id,
             service_user_token, webhook_secret, created_at)
           VALUES ($1,$2,'https://api.hibob.com',$3,$4,$5,$6,$7,$8)""",
        co_pk, run_id, LEGAL_NAME, COMPANY_ID, SERVICE_USER_ID, SERVICE_USER_TOKEN,
        WEBHOOK_SECRET, VNOW)

    for i, (eid, first, surname, email, active, created) in enumerate(EMPLOYEES):
        await pool.execute(
            """INSERT INTO app_hibob.employees
                (id, company_pk, employee_id, first_name, surname, second_name, full_name,
                 display_name, email, avatar_url, work_title, work_department, work_site,
                 work_start_date, work_is_manager, work_employee_id_in_company,
                 creation_date_time, modified, is_active, sort_key, is_historical)
               VALUES ($1,$2,$3,$4,$5,'',$6,$6,$7,$8,'Engineer','R&D','HQ',$9,$10,$11,
                       $12,$12,$13,$14,TRUE)""",
            uuid4(), co_pk, eid, first, surname, f"{first} {surname}", email,
            f"https://avatars.hibob.com/{eid}.png", created.date(), i == 1,
            str(i + 1), created, active, i)

    for sid, eidx, base, eff, cur, sk in SALARIES:
        await pool.execute(
            """INSERT INTO app_hibob.salaries
                (id, company_pk, salary_id, employee_id, base_value_cents, currency,
                 pay_period, pay_frequency, effective_date, is_current, creation_date,
                 modification_date, sort_key, is_historical)
               VALUES ($1,$2,$3,$4,$5,'USD','Annual','Monthly',$6,$7,$8,$8,$9,TRUE)""",
            uuid4(), co_pk, sid, EMPLOYEES[eidx][0], base, eff, cur,
            datetime.combine(eff, datetime.min.time(), tzinfo=timezone.utc), sk)

    for rid_, eidx, ctype, status, created_on, start, end in TIMEOFF:
        emp = EMPLOYEES[eidx]
        await pool.execute(
            """INSERT INTO app_hibob.timeoff_changes
                (id, company_pk, request_id, employee_id, employee_display_name,
                 employee_email, policy_type_display_name, change_type, status, created_on,
                 start_date, end_date, duration_unit, total_duration, total_cost,
                 request_type, sort_key, is_historical)
               VALUES ($1,$2,$3,$4,$5,$6,'Holiday',$7,$8,$9,$10,$11,'days',3,3,'days',$12,TRUE)""",
            uuid4(), co_pk, rid_, emp[0], f"{emp[1]} {emp[2]}", emp[3], ctype, status,
            created_on, start, end, int(created_on.timestamp()))
    return run_id


@pytest_asyncio.fixture(loop_scope="session")
async def hibob_client(pool, hibob_run):
    from spammers.hibob import state as h_state
    from spammers.hibob.app import create_app, _FORCED_429

    h_state._STATE = h_state.HibobMockState(pool=pool, run_id=hibob_run)
    _FORCED_429["count"] = 0
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://mock") as c:
        yield c
    h_state._STATE = None


@pytest.fixture
def hibob_auth() -> dict[str, str]:
    import base64
    cred = base64.b64encode(f"{SERVICE_USER_ID}:{SERVICE_USER_TOKEN}".encode()).decode()
    return {"Authorization": f"Basic {cred}"}
