"""Fixtures for the Carta mock fidelity suite (issuer cap-table read surface + the
OAuth client-credentials token mint). Carta is POLL-ONLY — no webhook.

Seeds a deterministic issuer with:
  * three STAKEHOLDERS (a founder + an employee + an institutional investor) —
    enough to walk a 2-page AIP token cursor at pageSize=2;
  * two SHARE_CLASSES (Common + Series Seed Preferred);
  * two OPTION_GRANTS (an ISO + an NSO on Common);
  * one CONVERTIBLE_NOTE (a SAFE held by the investor).

Wires the Carta ``state`` singleton + an ASGI client.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

ACCESS_TOKEN = "carta_at_fidelityMockToken0000000000000000000"
CLIENT_ID = "carta_id_fidelity0001"
CLIENT_SECRET = "carta_secret_fidelity0001"
ISSUER_ID = "70114"
LEGAL_NAME = "Alpen Labs Inc."

VNOW = datetime(2026, 2, 1, tzinfo=timezone.utc)
INC = VNOW - timedelta(days=1100)

# share class uuids (cross-ref ids)
SC_COMMON = "11111111-1111-4111-8111-111111111111"
SC_SEED = "22222222-2222-4222-8222-222222222222"

# (stakeholder_id, full_name, email, relationship, entity_type, country)
STAKEHOLDERS = [
    ("50000", "Ada Founder", "ada@alpenlabs.com", "FOUNDER", "INDIVIDUAL", "US"),
    ("50001", "Ben Engineer", "ben@alpenlabs.com", "EMPLOYEE", "INDIVIDUAL", "US"),
    ("60000", "Cascade Ventures", "contact@cascade.vc", "INVESTOR", "CORPORATION", "US"),
]
# (share_class_id, name, prefix, type, authorized, par, seniority)
SHARE_CLASSES = [
    (SC_COMMON, "Common Stock", "CS", "COMMON", 20_000_000, "0.0001", 0),
    (SC_SEED, "Series Seed Preferred", "SS", "PREFERRED", 4_500_000, "0.0001", 1),
]
# (grant_id, security_id, stakeholder_id, type, qty, vested, strike, days_before)
GRANTS = [
    ("25000", "aaaa1111-1111-4111-8111-111111111111", "50000", "NSO",
     600_000, 300_000, "0.32", 1000),
    ("25001", "bbbb2222-2222-4222-8222-222222222222", "50001", "ISO",
     20_000, 5_000, "1.05", 700),
]
# (note_id, security_id, stakeholder_id, label, principal_cents, cap_cents)
NOTES = [
    ("8000", "cccc3333-3333-4333-8333-333333333333", "60000", "SAFE-0001",
     250_000_00, 10_000_000_00),
]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def carta_run(pool) -> UUID:
    run_id = uuid4()
    await pool.execute(
        """INSERT INTO org.runs (id, size, runtime, seed, fyralis_tenant_id,
               fyralis_base_url, virtual_now, mode, speed_multiplier)
           VALUES ($1,'small','few_months',21,$2,'http://localhost:8000',$3,'frozen',1.0)""",
        run_id, uuid4(), VNOW)
    issuer_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_carta.issuers
            (id, run_id, base_url, issuer_id, legal_name, doing_business_as_name,
             website, client_id, client_secret, access_token, created_at)
           VALUES ($1,$2,'https://api.carta.com',$3,$4,'Alpen Labs',
                   'https://alpenlabs.com',$5,$6,$7,$8)""",
        issuer_pk, run_id, ISSUER_ID, LEGAL_NAME, CLIENT_ID, CLIENT_SECRET,
        ACCESS_TOKEN, INC)

    for i, (sid, name, email, rel, etype, country) in enumerate(STAKEHOLDERS):
        await pool.execute(
            """INSERT INTO app_carta.stakeholders
                (id, issuer_pk, stakeholder_id, full_name, email, employee_id,
                 relationship, entity_type, country, sort_key, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)""",
            uuid4(), issuer_pk, sid, name, email,
            f"E{1000 + i}" if etype == "INDIVIDUAL" else None,
            rel, etype, country, i, INC + timedelta(days=i))

    for i, (scid, name, prefix, sctype, auth, par, sen) in enumerate(SHARE_CLASSES):
        await pool.execute(
            """INSERT INTO app_carta.share_classes
                (id, issuer_pk, share_class_id, name, prefix, type, authorized_shares,
                 par_value, currency_code, seniority, pari_passu, sort_key, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'USD',$9,FALSE,$10,$11)""",
            uuid4(), issuer_pk, scid, name, prefix, sctype, auth, par, sen, i, INC)

    for i, (gid, secid, sh, otype, qty, vested, strike, days) in enumerate(GRANTS):
        issue = VNOW - timedelta(days=days)
        await pool.execute(
            """INSERT INTO app_carta.option_grants
                (id, issuer_pk, grant_id, security_id, share_class_id, stakeholder_id,
                 plan_name, stock_option_type, quantity, vested_quantity,
                 exercised_quantity, exercise_price, currency_code, early_exercisable,
                 issue_date, vesting_start_date, grant_expiration_date, last_modified,
                 sort_key, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,'2023 Equity Incentive Plan',$7,$8,$9,0,$10,
                       'USD',$11,$12,$12,$13,$14,$15,$16)""",
            uuid4(), issuer_pk, gid, secid, SC_COMMON, sh, otype, qty, vested, strike,
            (otype == "NSO"), issue.date(),
            (issue + timedelta(days=3650)).date(), issue + timedelta(days=30),
            i, issue)

    for i, (nid, secid, sh, label, principal, cap) in enumerate(NOTES):
        issued = INC + timedelta(days=40)
        await pool.execute(
            """INSERT INTO app_carta.convertible_notes
                (id, issuer_pk, note_id, security_id, stakeholder_id, security_label,
                 cash_paid_cents, price_cap_cents, currency_code, interest_rate,
                 discount_percentage, interest_compounding_period, day_count_basis,
                 issue_datetime, maturity_datetime, last_modified, sort_key, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'USD','0.00','20.00','ANNUALLY',
                       'COUNT_ACTUAL_365',$9,$10,$11,$12,$13)""",
            uuid4(), issuer_pk, nid, secid, sh, label, principal, cap, issued,
            issued + timedelta(days=730), issued + timedelta(days=10), i, issued)
    return run_id


@pytest_asyncio.fixture(loop_scope="session")
async def carta_client(pool, carta_run):
    from spammers.carta import state as c_state
    from spammers.carta.app import create_app, _FORCED_429

    c_state._STATE = c_state.CartaMockState(pool=pool, run_id=carta_run)
    _FORCED_429["count"] = 0
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://mock") as c:
        yield c
    c_state._STATE = None


@pytest.fixture
def carta_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {ACCESS_TOKEN}"}
