"""Fixtures for the Brex mock fidelity suite (cash/card accounts + transactions + webhook).

Seeds a deterministic organization with:
  * a primary CASH account + a secondary (non-primary) cash account, to exercise
    the cursor page envelope AND the /cash/primary selector;
  * a primary CARD account with a small statement period;
  * a known stream of cash transactions (PAYMENT credit, CARD_COLLECTION debit,
    INTEREST, FEE) with staggered ``posted_at`` and explicit transfer_ids, plus
    a small set of card transactions (PURCHASE positive, REFUND negative) with
    merchant data — enough to walk a 2-page cursor at limit=2.

Wires the Brex ``state`` singleton + an ASGI client.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

API_TOKEN = "bxt_fidelityMockToken_abc123def456"
# whsec_<base64(32 bytes)> — the mock base64-decodes the part after whsec_.
WEBHOOK_SECRET = "whsec_e/ETGy3XpsXyhQS7MKyfI3wG5wTGkq6MoNQpIWTdIyg="
LEGAL_NAME = "Alpen Labs Inc."
COMPANY_ID = "cmp_fidelity0000000000001"

VNOW = datetime(2026, 2, 1, tzinfo=timezone.utc)

CASH_PRIMARY = "dpsa_primarycash000000"
CASH_SECONDARY = "dpsa_secondarycash0000"
CARD_PRIMARY = "cuvs_primarycard000000"

# (txn_id, days_before_vnow, amount_cents(signed), type, transfer_id)
CASH_TXNS = [
    ("txn_cash01", 30, 250_000_000, "PAYMENT", "trnsfr_seed01"),       # funding credit
    ("txn_cash02", 20, -1_500_000, "CARD_COLLECTION", "trnsfr_seed02"),  # card collection debit
    ("txn_cash03", 18, 42_000, "INTEREST", None),                       # interest credit
    ("txn_cash04", 5, -3_500, "FEE", None),                             # wire fee debit
]
# (txn_id, days_before_vnow, amount_cents(signed), type, merchant, mcc)
CARD_TXNS = [
    ("txn_card01", 25, 120_000, "PURCHASE", "AMAZON WEB SERVICES", "5734"),
    ("txn_card02", 22, 8_900, "PURCHASE", "GITHUB", "5734"),
    ("txn_card03", 15, 45_000, "PURCHASE", "DATADOG", "7372"),
    ("txn_card04", 10, -12_000, "REFUND", "AMAZON WEB SERVICES", "5734"),
]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def brex_run(pool) -> UUID:
    run_id = uuid4()
    await pool.execute(
        """INSERT INTO org.runs (id, size, runtime, seed, fyralis_tenant_id,
               fyralis_base_url, virtual_now, mode, speed_multiplier)
           VALUES ($1,'small','few_months',13,$2,'http://localhost:8000',$3,'frozen',1.0)""",
        run_id, uuid4(), VNOW)
    org_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_brex.organizations
            (id, run_id, base_url, legal_business_name, company_id, api_token,
             webhook_secret, created_at)
           VALUES ($1,$2,'https://api.brex.com',$3,$4,$5,$6,$7)""",
        org_pk, run_id, LEGAL_NAME, COMPANY_ID, API_TOKEN, WEBHOOK_SECRET,
        VNOW - timedelta(days=900))

    cash_pk, cash2_pk, card_pk = uuid4(), uuid4(), uuid4()
    await pool.execute(
        """INSERT INTO app_brex.accounts
            (id, org_pk, account_id, kind, name, status, account_number, routing_number,
             currency, current_balance_cents, available_balance_cents, is_primary,
             sort_key, created_at)
           VALUES ($1,$2,$3,'cash','Brex Cash','ACTIVE','123456789','121145349','USD',
                   246538500, 246538500, TRUE, 0, $4)""",
        cash_pk, org_pk, CASH_PRIMARY, VNOW - timedelta(days=900))
    await pool.execute(
        """INSERT INTO app_brex.accounts
            (id, org_pk, account_id, kind, name, status, account_number, routing_number,
             currency, current_balance_cents, available_balance_cents, is_primary,
             sort_key, created_at)
           VALUES ($1,$2,$3,'cash','Brex Reserve','ACTIVE','987654321','121145349','USD',
                   100000000, 100000000, FALSE, 1, $4)""",
        cash2_pk, org_pk, CASH_SECONDARY, VNOW - timedelta(days=900))
    await pool.execute(
        """INSERT INTO app_brex.accounts
            (id, org_pk, account_id, kind, status, currency, current_balance_cents,
             available_balance_cents, account_limit_cents, statement_start, statement_end,
             is_primary, sort_key, created_at)
           VALUES ($1,$2,$3,'card','ACTIVE','USD', 161900, 49838100, 50000000,
                   '2026-02-01','2026-03-01', TRUE, 0, $4)""",
        card_pk, org_pk, CARD_PRIMARY, VNOW - timedelta(days=900))

    for i, (tid, days, amt, ttype, transfer_id) in enumerate(CASH_TXNS):
        posted = VNOW - timedelta(days=days)
        initiated = posted - timedelta(days=1)
        await pool.execute(
            """INSERT INTO app_brex.transactions
                (id, account_pk, account_kind, txn_id, description, amount_cents, currency,
                 txn_type, initiated_at, posted_at, transfer_id, sort_key, is_historical)
               VALUES ($1,$2,'cash',$3,$4,$5,'USD',$6,$7,$8,$9,$10,TRUE)""",
            uuid4(), cash_pk, tid, f"{ttype} {tid}", amt, ttype, initiated, posted,
            transfer_id, i)

    for i, (tid, days, amt, ttype, merchant, mcc) in enumerate(CARD_TXNS):
        posted = VNOW - timedelta(days=days)
        initiated = posted - timedelta(days=1)
        await pool.execute(
            """INSERT INTO app_brex.transactions
                (id, account_pk, account_kind, txn_id, description, amount_cents, currency,
                 txn_type, initiated_at, posted_at, card_id, merchant_raw_descriptor,
                 merchant_mcc, merchant_country, expense_id, sort_key, is_historical)
               VALUES ($1,$2,'card',$3,$4,$5,'USD',$6,$7,$8,'card_phys001',$9,$10,'USA',
                       $11,$12,TRUE)""",
            uuid4(), card_pk, tid, merchant.title(), amt, ttype, initiated, posted,
            merchant, mcc, f"expense_{tid}", i)
    return run_id


@pytest_asyncio.fixture(loop_scope="session")
async def brex_client(pool, brex_run):
    from spammers.brex import state as b_state
    from spammers.brex.app import create_app, _FORCED_429

    b_state._STATE = b_state.BrexMockState(pool=pool, run_id=brex_run)
    _FORCED_429["count"] = 0
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://mock") as c:
        yield c
    b_state._STATE = None


@pytest.fixture
def brex_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_TOKEN}"}
