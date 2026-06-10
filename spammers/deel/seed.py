"""Realistic Deel corpus seeding.

Deel is a NET-NEW Tier-C source: the frozen run has no Deel corpus, so we model
realistic content ourselves (the brief sanctions this, like ashby projecting the
org chart and brex projecting QB finance). Deel is the company's **global payroll
/ contractor-payments** layer, so we project the run's people into Deel:

  * one **CONTRACT** per person (a mix of contractor / EOR-employee / direct
    employee types across a spread of worker countries) — the contract-state
    stream the planner shards on;
  * a monthly **INVOICE** stream per contract (the paid worker-invoice history —
    Deel's real "payment" surface; each invoice carries ``contract_id``). Most
    invoices are ``paid``; the latest one or two are ``pending`` / ``processing``.

Money is stored as integer cents internally and rendered as a decimal STRING in
major units on the wire. Everything is deterministic off the run seed. Idempotent:
a second call after the org row exists is a no-op.
"""
from __future__ import annotations

import string
from datetime import datetime, timedelta, timezone
from random import Random
from typing import Optional
from uuid import UUID, uuid4

import asyncpg

# Seed-stable org identity (hand these to the ingest-client / memory).
LEGAL_BUSINESS_NAME = "Alpen Labs Inc."
ORGANIZATION_ID = "org_alpenlabs00000000001"
API_TOKEN = "deel_live_8sKpQ2mZ7xR4tV9bN6wF3hJ1cD5aL0gY"
WEBHOOK_SECRET = "whk_3f9c1e7a52b84d06c1a9f4e2d7b605c8e3a0f1d6b9c2e5a8f4d7b0c3e6a9f2d5"

_ALNUM = string.ascii_lowercase + string.digits
# (country_code, currency, vat_rate_bps) — Deel's global worker spread.
_COUNTRIES = [
    ("US", "USD", 0), ("GB", "GBP", 2000), ("IN", "USD", 0), ("BR", "USD", 0),
    ("DE", "EUR", 1900), ("CA", "USD", 0), ("NG", "USD", 0), ("PH", "USD", 0),
    ("ES", "EUR", 2100), ("PL", "EUR", 2300), ("MX", "USD", 0), ("AR", "USD", 0),
]
_CONTRACT_TYPES = [
    "ongoing_time_based", "pay_as_you_go_time_based", "eor", "employee",
]


def _opaque(rng: Random, n: int = 13) -> str:
    return "".join(rng.choice(_ALNUM) for _ in range(n))


async def seed_deel(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    at: Optional[datetime] = None,
) -> dict[str, int]:
    """Provision the org + one contract per person + a monthly invoice stream.

    Idempotent. Returns ``{"contracts": C, "invoices": N}``."""
    existing = await pool.fetchval(
        "SELECT id FROM app_deel.organizations WHERE run_id = $1", run_id)
    if existing is not None:
        return {"contracts": 0, "invoices": 0}

    seed_row = await pool.fetchrow(
        "SELECT seed, virtual_now FROM org.runs WHERE id = $1", run_id)
    if seed_row is None:
        raise LookupError(f"no run {run_id}")
    rng = Random(int(seed_row["seed"]) ^ 0x6465_656C)  # 'deel'
    now = at or seed_row["virtual_now"] or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    org_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_deel.organizations
            (id, run_id, base_url, legal_business_name, organization_id, api_token,
             webhook_secret, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
        org_pk, run_id, "https://api.letsdeel.com/rest/v2", LEGAL_BUSINESS_NAME,
        ORGANIZATION_ID, API_TOKEN, WEBHOOK_SECRET, now - timedelta(days=900))

    people = await pool.fetch(
        "SELECT full_name, email, role, level, started_at, ended_at "
        "FROM org.people WHERE run_id = $1 ORDER BY started_at, email", run_id)

    contracts = 0
    invoices = 0
    window_floor = now - timedelta(days=18 * 31)  # cap the invoice tail to ~18 months
    for idx, p in enumerate(people):
        country, currency, vat_bps = _COUNTRIES[idx % len(_COUNTRIES)]
        ctype = _CONTRACT_TYPES[idx % len(_CONTRACT_TYPES)]
        started = p["started_at"]
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        eff_start = max(started, window_floor)
        ended = p["ended_at"]
        if ended is not None and ended.tzinfo is None:
            ended = ended.replace(tzinfo=timezone.utc)

        # Contract status: completed if the worker left, onboarding if very recent,
        # else in_progress (the steady state).
        if ended is not None and ended <= now:
            status = "completed"
            term_date = ended.date()
        elif started > now - timedelta(days=20):
            status = "onboarding"
            term_date = None
        else:
            status = "in_progress"
            term_date = None

        monthly_cents = rng.randint(4_000, 18_000) * 100
        contract_id = "ctr_" + _opaque(rng)
        contract_pk = uuid4()
        await pool.execute(
            """INSERT INTO app_deel.contracts
                (id, org_pk, contract_id, type, title, status, worker_name, worker_email,
                 worker_country, client_name, job_title, comp_amount_cents, comp_currency,
                 comp_frequency, comp_scale, external_id, is_archived, start_date,
                 termination_date, created_at, updated_at, sort_key, is_historical)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,'monthly','monthly',
                       $14,FALSE,$15,$16,$17,$18,$19,TRUE)""",
            contract_pk, org_pk, contract_id, ctype,
            f"{p['role']} — {p['full_name']}", status, p["full_name"], p["email"],
            country, LEGAL_BUSINESS_NAME, p["role"], monthly_cents, currency,
            f"ext_{p['email'].split('@')[0]}", started.date(), term_date,
            started, ended or now, idx)
        contracts += 1

        # Monthly invoices from the effective start to ``now``.
        month = eff_start.replace(day=1, hour=9, minute=0, second=0, microsecond=0)
        seq = 0
        stop = ended if (ended is not None and ended < now) else now
        while month <= stop:
            seq += 1
            amount = monthly_cents + rng.randint(-200, 800) * 100
            vat = (amount * vat_bps) // 10_000
            fee = max(500, amount // 100)  # Deel platform fee
            total = amount + vat + fee
            issued = month
            # Recent unpaid tail: the latest invoice (and one before) stays pending/processing.
            months_back = (now.year - month.year) * 12 + (now.month - month.month)
            if status != "completed" and months_back == 0:
                inv_status, paid_at = "pending", None
            elif status != "completed" and months_back == 1 and rng.random() < 0.5:
                inv_status, paid_at = "processing", None
            else:
                inv_status = "paid"
                paid_at = issued + timedelta(days=rng.randint(2, 6))
            is_overdue = inv_status == "pending" and issued < now - timedelta(days=35)
            await pool.execute(
                """INSERT INTO app_deel.invoices
                    (id, org_pk, contract_pk, invoice_id, contract_id, label, total_cents,
                     amount_cents, vat_cents, deel_fee_cents, currency, status, issued_at,
                     due_date, paid_at, created_at, is_overdue, recipient_legal_entity_id,
                     sort_key, is_historical)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,TRUE)""",
                uuid4(), org_pk, contract_pk, "inv_" + _opaque(rng), contract_id,
                f"INV-{month.year}-{seq:02d} {p['full_name']}", total, amount, vat, fee,
                currency, inv_status, issued, issued + timedelta(days=14), paid_at,
                issued, is_overdue, "le_" + _opaque(rng, 12),
                int(issued.timestamp()))
            invoices += 1
            month = (month + timedelta(days=32)).replace(day=1)

    return {"contracts": contracts, "invoices": invoices}
