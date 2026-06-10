"""Fixtures for the Mercury mock fidelity suite (accounts/transactions + webhook).

Seeds a deterministic organization with three bank accounts (checking, savings,
treasury) and a known transaction stream with staggered ``created_at`` values —
four checking transactions inside the run's 30-day default window plus one well
outside it (to exercise the default-window behaviour), a credit and several
debits, and one ``pending`` (postedAt null) transaction. Wires the Mercury
``state`` singleton + an ASGI client.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

API_TOKEN = "secret-token:mercury_production_fidelityMockToken_abc123"
WEBHOOK_SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
LEGAL_NAME = "Alpen Labs Inc."

# Fixed virtual clock so the 30-day default transaction window is deterministic.
VNOW = datetime(2026, 2, 1, tzinfo=timezone.utc)

ACCT_CHECKING = UUID("11111111-1111-4111-8111-111111111111")
ACCT_SAVINGS = UUID("22222222-2222-4222-8222-222222222222")
ACCT_TREASURY = UUID("33333333-3333-4333-8333-333333333333")

# (txn_id, account, days_before_vnow, amount_cents(signed), status, kind, counterparty, pending)
TXNS = [
    (UUID("aaaa0001-0000-4000-8000-000000000001"), ACCT_CHECKING, 1, -120000, "pending",
     "externalTransfer", "Amazon Web Services", True),    # newest, pending, debit
    (UUID("aaaa0002-0000-4000-8000-000000000002"), ACCT_CHECKING, 2, -250000, "sent",
     "externalTransfer", "Vercel", False),
    (UUID("aaaa0003-0000-4000-8000-000000000003"), ACCT_CHECKING, 5, 50000000, "sent",
     "incomingDomesticWire", "Paradigm", False),          # credit (positive)
    (UUID("aaaa0004-0000-4000-8000-000000000004"), ACCT_CHECKING, 10, -4500, "sent",
     "debitCardTransaction", "GitHub", False),            # small => card
    (UUID("aaaa0005-0000-4000-8000-000000000005"), ACCT_CHECKING, 240, -800000, "sent",
     "externalTransfer", "WeWork", False),                # OUTSIDE the 30-day window
    (UUID("bbbb0001-0000-4000-8000-000000000001"), ACCT_SAVINGS, 8, 10000000, "sent",
     "internalTransfer", "Alpen Labs Checking", False),
    (UUID("cccc0001-0000-4000-8000-000000000001"), ACCT_TREASURY, 30, 100000000, "sent",
     "treasuryTransfer", "Alpen Labs Checking", False),
]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def mercury_run(pool) -> UUID:
    run_id = uuid4()
    await pool.execute(
        """INSERT INTO org.runs (id, size, runtime, seed, fyralis_tenant_id,
               fyralis_base_url, virtual_now, mode, speed_multiplier)
           VALUES ($1,'small','few_months',11,$2,'http://localhost:8000',$3,'frozen',1.0)""",
        run_id, uuid4(), VNOW)
    org_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_mercury.organizations
            (id, run_id, base_url, legal_business_name, api_token, webhook_secret, created_at)
           VALUES ($1,$2,'https://api.mercury.com/api/v1',$3,$4,$5,$6)""",
        org_pk, run_id, LEGAL_NAME, API_TOKEN, WEBHOOK_SECRET, VNOW - timedelta(days=900))

    accts = [
        (ACCT_CHECKING, "Alpen Labs Checking", "checking", 0, 4521900050),
        (ACCT_SAVINGS, "Alpen Labs Savings", "savings", 1, 5000000),
        (ACCT_TREASURY, "Alpen Labs Treasury", "treasury", 2, 250000000),
    ]
    acct_pks: dict[UUID, UUID] = {}
    for aid, name, kind, sort_key, bal in accts:
        pk = uuid4()
        acct_pks[aid] = pk
        await pool.execute(
            """INSERT INTO app_mercury.accounts
                (id, org_pk, account_id, name, nickname, account_number, routing_number,
                 status, type, kind, available_balance_cents, current_balance_cents,
                 legal_business_name, dashboard_link, can_receive_transactions, sort_key,
                 created_at)
               VALUES ($1,$2,$3,$4,NULL,$5,'021000021','active','mercury',$6,$7,$7,$8,$9,
                       TRUE,$10,$11)""",
            pk, org_pk, aid, name, f"90{sort_key}1234567{sort_key}", kind, bal,
            LEGAL_NAME, f"https://mercury.com/transactions/{aid}", sort_key,
            VNOW - timedelta(days=900))

    for (tid, aid, days, amt, status, kind, cp, pending) in TXNS:
        created = VNOW - timedelta(days=days)
        posted = None if pending else created + timedelta(days=1)
        edd = created + timedelta(days=1)
        await pool.execute(
            """INSERT INTO app_mercury.transactions
                (id, account_pk, txn_id, amount_cents, status, kind, counterparty_id,
                 counterparty_name, counterparty_nickname, external_memo, dashboard_link,
                 version, created_at, posted_at, estimated_delivery_date, is_historical)
               VALUES ($1,$2,$1,$3,$4,$5,$6,$7,NULL,$8,$9,1,$10,$11,$12,TRUE)""",
            tid, acct_pks[aid], amt, status, kind, uuid4(), cp, f"Payment to {cp}",
            f"https://mercury.com/transactions/{tid}", created, posted, edd)
    return run_id


@pytest_asyncio.fixture(loop_scope="session")
async def mercury_client(pool, mercury_run):
    from spammers.mercury import state as m_state
    from spammers.mercury.app import create_app, _FORCED_429

    m_state._STATE = m_state.MercuryMockState(pool=pool, run_id=mercury_run)
    _FORCED_429["count"] = 0
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://mock") as c:
        yield c
    m_state._STATE = None


@pytest.fixture
def mercury_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_TOKEN}"}
