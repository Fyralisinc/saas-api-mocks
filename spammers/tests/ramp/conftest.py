"""Fixtures for the Ramp mock fidelity suite (transactions/reimbursements/cards/
users + the OAuth token mint + the X-Ramp-Signature webhook).

Seeds a deterministic organization with:
  * three USERS + three CARDS (entity-attribution backbone);
  * four TRANSACTIONS with staggered ``settlement_date`` + ``sort_key`` (three
    CLEARED, one DECLINED) — enough to walk a 2-page KEYSET cursor at page_size=2;
  * two REIMBURSEMENTS (OUT_OF_POCKET + MILEAGE).

Wires the Ramp ``state`` singleton + an ASGI client.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

ACCESS_TOKEN = "ramp_business_tok_fidelityMockToken000000000000000"
WEBHOOK_SECRET = "rwhs_fidelity00000000000000000000000000"
BUSINESS_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
CLIENT_ID = "ramp_id_fidelity0001"
CLIENT_SECRET = "ramp_secret_fidelity0001"
LEGAL_NAME = "Alpen Labs Inc."

VNOW = datetime(2026, 2, 1, tzinfo=timezone.utc)

USER_A = "11111111-1111-4111-8111-111111111111"
USER_B = "22222222-2222-4222-8222-222222222222"
USER_C = "33333333-3333-4333-8333-333333333333"
CARD_A = "aaaa1111-1111-4111-8111-111111111111"

# (txn_id, days_before_vnow, amount_cents, state, sync_status, merchant, mcc, sk_id, sk_name)
TXNS = [
    ("c1111111-1111-4111-8111-111111111111", 30, 12_000_000, "CLEARED", "SYNCED",
     "Amazon Web Services", "5734", 33, "Computer Software"),
    ("c2222222-2222-4222-8222-222222222222", 22, 89_000, "CLEARED", "SYNCED",
     "GitHub", "5734", 33, "Computer Software"),
    ("c3333333-3333-4333-8333-333333333333", 15, 45_000, "CLEARED", "SYNC_READY",
     "Datadog", "7372", 34, "Cloud Computing & SaaS"),
    ("c4444444-4444-4444-8444-444444444444", 8, 250_00, "DECLINED", "NOT_SYNC_READY",
     "WeWork", "6513", 12, "Office Rent"),
]
# (reimb_id, days_before, amount_cents, type, merchant)
REIMBS = [
    ("d1111111-1111-4111-8111-111111111111", 20, 8_500, "OUT_OF_POCKET", "Starbucks"),
    ("d2222222-2222-4222-8222-222222222222", 10, 42_000, "MILEAGE", "Personal Vehicle"),
]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def ramp_run(pool) -> UUID:
    run_id = uuid4()
    await pool.execute(
        """INSERT INTO org.runs (id, size, runtime, seed, fyralis_tenant_id,
               fyralis_base_url, virtual_now, mode, speed_multiplier)
           VALUES ($1,'small','few_months',18,$2,'http://localhost:8000',$3,'frozen',1.0)""",
        run_id, uuid4(), VNOW)
    org_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_ramp.organizations
            (id, run_id, base_url, legal_business_name, business_id, client_id,
             client_secret, access_token, webhook_secret, created_at)
           VALUES ($1,$2,'https://api.ramp.com',$3,$4,$5,$6,$7,$8,$9)""",
        org_pk, run_id, LEGAL_NAME, BUSINESS_ID, CLIENT_ID, CLIENT_SECRET,
        ACCESS_TOKEN, WEBHOOK_SECRET, VNOW - timedelta(days=900))

    for i, (uid, name, email, is_mgr) in enumerate([
            (USER_A, "Alice Anderson", "alice@alpenlabs.com", True),
            (USER_B, "Bob Brown", "bob@alpenlabs.com", False),
            (USER_C, "Carol Clark", "carol@alpenlabs.com", False)]):
        first, _, last = name.partition(" ")
        await pool.execute(
            """INSERT INTO app_ramp.users
                (id, org_pk, user_id, first_name, last_name, email, role, status,
                 department_name, is_manager, employee_id, business_id, sort_key, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,'USER_ACTIVE','Engineering',$8,$9,$10,$11,$12)""",
            uuid4(), org_pk, uid, first, last,
            email, "BUSINESS_ADMIN" if is_mgr else "BUSINESS_USER", is_mgr,
            f"E{100 + i}", BUSINESS_ID, i, VNOW - timedelta(days=600))

    await pool.execute(
        """INSERT INTO app_ramp.cards
            (id, org_pk, card_id, display_name, last_four, cardholder_id, cardholder_name,
             expiration, is_physical, state, sort_key, created_at)
           VALUES ($1,$2,$3,'Alice Card','4242',$4,'Alice Anderson','03-29',TRUE,'ACTIVE',0,$5)""",
        uuid4(), org_pk, CARD_A, USER_A, VNOW - timedelta(days=600))

    for i, (tid, days, amt, state, sync, merch, mcc, sk_id, sk_name) in enumerate(TXNS):
        settled = VNOW - timedelta(days=days)
        created = settled - timedelta(days=1)
        await pool.execute(
            """INSERT INTO app_ramp.transactions
                (id, org_pk, txn_id, amount_cents, currency_code, state, sync_status,
                 card_id, card_present, user_id, cardholder_name, merchant_id, merchant_name,
                 merchant_category_code, sk_category_id, sk_category_name,
                 user_transaction_time, accounting_date, settlement_date, synced_at,
                 sort_key, is_historical)
               VALUES ($1,$2,$3,$4,'USD',$5,$6,$7,FALSE,$8,'Alice Anderson',$9,$10,$11,
                       $12,$13,$14,$15,$16,$17,$18,TRUE)""",
            uuid4(), org_pk, tid, amt, state, sync, CARD_A, USER_A,
            f"mrch_{i}", merch, mcc, sk_id, sk_name, created,
            settled, settled, settled + timedelta(days=2), i)

    for i, (rid, days, amt, rtype, merch) in enumerate(REIMBS):
        created = VNOW - timedelta(days=days)
        await pool.execute(
            """INSERT INTO app_ramp.reimbursements
                (id, org_pk, reimb_id, amount_cents, currency, state, type, direction,
                 user_id, user_email, user_full_name, merchant, merchant_id,
                 transaction_date, sync_status, memo, created_at, updated_at,
                 submitted_at, approved_at, synced_at, sort_key, is_historical)
               VALUES ($1,$2,$3,$4,'USD','REIMBURSED',$5,'BUSINESS_TO_USER',$6,$7,$8,$9,
                       $10,$11,'SYNCED',$12,$13,$13,$13,$13,$13,$14,TRUE)""",
            uuid4(), org_pk, rid, amt, rtype, USER_B, "bob@alpenlabs.com", "Bob Brown",
            merch, f"mrch_r{i}", created.date(),
            f"{rtype} expense", created, i)
    return run_id


@pytest_asyncio.fixture(loop_scope="session")
async def ramp_client(pool, ramp_run):
    from spammers.ramp import state as r_state
    from spammers.ramp.app import create_app, _FORCED_429

    r_state._STATE = r_state.RampMockState(pool=pool, run_id=ramp_run)
    _FORCED_429["count"] = 0
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://mock") as c:
        yield c
    r_state._STATE = None


@pytest.fixture
def ramp_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {ACCESS_TOKEN}"}
