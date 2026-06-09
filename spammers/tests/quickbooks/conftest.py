"""Fixtures for the QuickBooks Online mock fidelity suite (QBO Accounting API v3).

Seeds a deterministic company (realm), a small chart of accounts, two vendors,
two purchases (-> two Bills + two BillPayments), and one grant deposit (-> one
Invoice + one Payment) with staggered ``created_at``. Wires the QuickBooks
``state`` singleton + an ASGI client.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

REALM_ID = "9341453412700001"
ACCESS_TOKEN = "qbo-access-token-fidelity"
_T0 = datetime(2026, 1, 10, 9, 0, tzinfo=timezone.utc)

# (purchase_id, vendor_idx, amount_usd, expense_num, offset_h)
PURCHASES = [
    ("opex-aaa1", 0, 1800, "5310", 0),
    ("opex-bbb2", 1, 4200, "5000", 2),
]
# (deposit_id, amount_usd, lead, round_kind, offset_h)
GRANT = ("dep-grant1", 250000, "Starknet Foundation", "grant", 4)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def qb_run(pool) -> UUID:
    run_id = uuid4()
    await pool.execute(
        """INSERT INTO org.runs (id, size, runtime, seed, fyralis_tenant_id,
               fyralis_base_url, virtual_now, mode, speed_multiplier)
           VALUES ($1,'small','few_months',9,$2,'http://localhost:8000',now(),'frozen',1.0)""",
        run_id, uuid4())
    company_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_quickbooks.companies
            (id, run_id, realm_id, company_name, legal_name, country, currency,
             fiscal_year_start, created_at)
           VALUES ($1,$2,$3,'Alpen Labs','Alpen Labs, Inc.','US','USD','January',$4)""",
        company_pk, run_id, REALM_ID, _T0)
    accts = {"1000": ("Operating Bank", "Bank"), "2000": ("Accounts Payable (A/P)", "AccountsPayable"),
             "5000": ("Expense", "Expense"), "5310": ("Insurance", "Expense")}
    for num, (name, typ) in accts.items():
        await pool.execute(
            """INSERT INTO app_quickbooks.accounts
                (id, company_pk, account_id, account_number, name, type, subtype, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,'',$7)""",
            uuid4(), company_pk, f"acct-{num}", num, name, typ, _T0)
    vendor_pks = []
    for i, name in enumerate(("Justworks", "AWS")):
        vpk = uuid4(); vendor_pks.append(vpk)
        await pool.execute(
            """INSERT INTO app_quickbooks.vendors
                (id, company_pk, vendor_id, display_name, active, currency, created_at)
               VALUES ($1,$2,$3,$4,TRUE,'USD',$5)""",
            vpk, company_pk, f"vendor-{i}", name, _T0)
    acct_pk = {}
    for num in accts:
        acct_pk[num] = await pool.fetchval(
            "SELECT id FROM app_quickbooks.accounts WHERE company_pk=$1 AND account_number=$2",
            company_pk, num)
    for pid, vidx, amt, exp_num, off in PURCHASES:
        await pool.execute(
            """INSERT INTO app_quickbooks.purchases
                (id, company_pk, purchase_id, txn_date, amount_cents, vendor_pk,
                 expense_account_pk, payment_account_pk, category, memo, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'opex','',$9)""",
            uuid4(), company_pk, pid, (_T0 + timedelta(hours=off)).date(), amt * 100,
            vendor_pks[vidx], acct_pk[exp_num], acct_pk["1000"], _T0 + timedelta(hours=off))
    dep_id, amt, lead, kind, off = GRANT
    await pool.execute(
        """INSERT INTO app_quickbooks.deposits
            (id, company_pk, deposit_id, txn_date, amount_cents, deposit_to_account_pk,
             round_kind, lead, memo, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'',$9)""",
        uuid4(), company_pk, dep_id, (_T0 + timedelta(hours=off)).date(), amt * 100,
        acct_pk["1000"], kind, lead, _T0 + timedelta(hours=off))
    return run_id


@pytest_asyncio.fixture(loop_scope="session")
async def qb_client(pool, qb_run):
    from spammers.quickbooks import state as q_state
    from spammers.quickbooks.app import create_app, _FORCED_429

    q_state._STATE = q_state.QuickBooksMockState(pool=pool, run_id=qb_run)
    _FORCED_429["count"] = 0
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://mock") as c:
        yield c
    q_state._STATE = None


def auth_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {ACCESS_TOKEN}"}


@pytest.fixture
def qb_auth() -> dict[str, str]:
    return auth_header()
