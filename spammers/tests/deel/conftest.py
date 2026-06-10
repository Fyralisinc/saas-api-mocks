"""Fixtures for the Deel mock fidelity suite (contracts + invoices + webhook).

Seeds a deterministic organization with:
  * three CONTRACTS across a spread of types/statuses/countries — enough to walk a
    2-page CURSOR page at limit=2 and to exercise the workflow status enum;
  * a known INVOICE set (paid + pending + processing) so the suite can pin the
    paid-only-vs-status=all filter, the {data, page:{offset,total_rows,
    items_per_page,cursor}} hybrid envelope, the decimal-string money, and the
    issued-date window.

Wires the Deel ``state`` singleton + an ASGI client.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

API_TOKEN = "deel_live_fidelityMockToken_abc123"
WEBHOOK_SECRET = "whk_fidelity0000000000000000000000000000000000000000000000000000000"
LEGAL_NAME = "Alpen Labs Inc."
ORGANIZATION_ID = "org_fidelity00000000001"

VNOW = datetime(2026, 4, 1, tzinfo=timezone.utc)

# (contract_id, type, status, country, currency, comp_cents, created_at, term_date)
CONTRACTS = [
    ("ctr_alpha01", "ongoing_time_based", "in_progress", "US", "USD", 900_000,
     datetime(2025, 6, 1, tzinfo=timezone.utc), None),
    ("ctr_bravo02", "eor", "completed", "GB", "GBP", 1_100_000,
     datetime(2025, 7, 1, tzinfo=timezone.utc), "2026-02-15"),
    ("ctr_charlie3", "employee", "onboarding", "DE", "EUR", 1_300_000,
     datetime(2025, 8, 1, tzinfo=timezone.utc), None),
]
# (invoice_id, contract_idx, status, amount_cents, issued, paid_at)
INVOICES = [
    ("inv_p001", 0, "paid", 900_000, datetime(2026, 1, 15, tzinfo=timezone.utc),
     datetime(2026, 1, 18, tzinfo=timezone.utc)),
    ("inv_p002", 0, "paid", 920_000, datetime(2026, 2, 15, tzinfo=timezone.utc),
     datetime(2026, 2, 18, tzinfo=timezone.utc)),
    ("inv_p003", 1, "paid", 1_100_000, datetime(2026, 1, 20, tzinfo=timezone.utc),
     datetime(2026, 1, 24, tzinfo=timezone.utc)),
    ("inv_pend4", 0, "pending", 900_000, datetime(2026, 3, 15, tzinfo=timezone.utc), None),
    ("inv_proc5", 2, "processing", 1_300_000, datetime(2026, 3, 10, tzinfo=timezone.utc), None),
]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def deel_run(pool) -> UUID:
    run_id = uuid4()
    await pool.execute(
        """INSERT INTO org.runs (id, size, runtime, seed, fyralis_tenant_id,
               fyralis_base_url, virtual_now, mode, speed_multiplier)
           VALUES ($1,'small','few_months',14,$2,'http://localhost:8000',$3,'frozen',1.0)""",
        run_id, uuid4(), VNOW)
    org_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_deel.organizations
            (id, run_id, base_url, legal_business_name, organization_id, api_token,
             webhook_secret, created_at)
           VALUES ($1,$2,'https://api.letsdeel.com/rest/v2',$3,$4,$5,$6,$7)""",
        org_pk, run_id, LEGAL_NAME, ORGANIZATION_ID, API_TOKEN, WEBHOOK_SECRET,
        VNOW)

    pks: list[UUID] = []
    for i, (cid, ctype, status, country, currency, comp, created, term) in enumerate(CONTRACTS):
        cpk = uuid4()
        pks.append(cpk)
        await pool.execute(
            """INSERT INTO app_deel.contracts
                (id, org_pk, contract_id, type, title, status, worker_name, worker_email,
                 worker_country, client_name, job_title, comp_amount_cents, comp_currency,
                 comp_frequency, comp_scale, external_id, is_archived, start_date,
                 termination_date, created_at, updated_at, sort_key, is_historical)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'Engineer',$11,$12,'monthly','monthly',
                       $13,FALSE,$14,$15,$16,$16,$17,TRUE)""",
            cpk, org_pk, cid, ctype, f"Engineer {cid}", status, f"Worker {i}",
            f"worker{i}@example.com", country, LEGAL_NAME, comp, currency,
            f"ext_{cid}", created.date(),
            date.fromisoformat(term) if term else None, created, i)

    for i, (iid, cidx, status, amount, issued, paid_at) in enumerate(INVOICES):
        cid = CONTRACTS[cidx][0]
        fee = max(500, amount // 100)
        total = amount + fee
        await pool.execute(
            """INSERT INTO app_deel.invoices
                (id, org_pk, contract_pk, invoice_id, contract_id, label, total_cents,
                 amount_cents, vat_cents, deel_fee_cents, currency, status, issued_at,
                 due_date, paid_at, created_at, is_overdue, recipient_legal_entity_id,
                 sort_key, is_historical)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,0,$9,$10,$11,$12,$12,$13,$12,FALSE,$14,$15,TRUE)""",
            uuid4(), org_pk, pks[cidx], iid, cid, f"INV {iid}", total, amount, fee,
            CONTRACTS[cidx][4], status, issued, paid_at, f"le_{iid}",
            int(issued.timestamp()))
    return run_id


@pytest_asyncio.fixture(loop_scope="session")
async def deel_client(pool, deel_run):
    from spammers.deel import state as d_state
    from spammers.deel.app import create_app, _FORCED_429

    d_state._STATE = d_state.DeelMockState(pool=pool, run_id=deel_run)
    _FORCED_429["count"] = 0
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://mock") as c:
        yield c
    d_state._STATE = None


@pytest.fixture
def deel_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_TOKEN}"}
