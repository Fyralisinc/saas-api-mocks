"""Realistic Mercury corpus seeding.

Mercury is a NET-NEW Tier-C source: the frozen run has no Mercury corpus, so we
model realistic banking content ourselves (the brief sanctions this, like grafana
synthesizing annotations and QuickBooks projecting the four entities). The honest
move for a finance source is to project the **same underlying cash movements** the
run's accounting system already records — a real company has both QuickBooks AND
Mercury showing the one set of flows. So we derive the transaction stream from the
existing ``app_quickbooks`` finance rows:

  * each **purchase** (vendor money-out, AP) -> a checking **debit** (negative
    amount), kind ``externalTransfer`` (or ``debitCardTransaction`` for small ones),
    counterparty = the vendor;
  * each **deposit** (money-in: grants + equity rounds) -> a checking **credit**
    (positive amount), kind ``incomingDomesticWire``, counterparty = the lead/grantor;
  * a handful of ``internalTransfer``/``treasuryTransfer`` sweeps between the
    checking, savings and treasury accounts.

The most recent few checking transactions are left ``pending`` (postedAt null) —
the realistic in-flight tail — so a consumer observes both statuses. Everything is
deterministic off the run seed. If no QuickBooks corpus is present the stream is
synthesized from a small catalogue. Idempotent: a second call after the org row
exists is a no-op.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from random import Random
from typing import Optional
from uuid import UUID

import asyncpg

# Seed-stable org identity (hand these to the ingest-client / memory).
LEGAL_BUSINESS_NAME = "Alpen Labs Inc."
API_TOKEN = "secret-token:mercury_production_aGmZ4kP9xWqT2sLcV7nR8yB3"
WEBHOOK_SECRET = "8c41f0a9d2e6b574c0193af8275e6b4d1c9e0a3f2b5d8c7e6f1a0b9c8d7e6f5a4"

# The three bank accounts a funded startup runs.
_ACCOUNTS = [
    # (name, kind, sort_key, routing, base_cents)
    ("Alpen Labs Checking", "checking", 0, "021000021", 0),
    ("Alpen Labs Savings", "savings", 1, "021000021", 5_000_000),
    ("Alpen Labs Treasury", "treasury", 2, "021000021", 250_000_000),
]
_FALLBACK_VENDORS = [
    "Amazon Web Services", "Google Cloud", "GitHub", "Vercel", "Datadog",
    "Notion Labs", "Linear", "Ramp", "Gusto", "Carta", "WeWork", "Brex",
]
_FALLBACK_FUNDERS = [
    ("Paradigm", "grant"), ("a16z crypto", "seed"), ("Electric Capital", "strategic"),
]


def _det_uuid(rng: Random) -> UUID:
    return UUID(int=rng.getrandbits(128))


def _acct_number(rng: Random) -> str:
    return "".join(str(rng.randint(0, 9)) for _ in range(10))


async def _quickbooks_flows(pool: asyncpg.Pool, run_id: UUID):
    """Return (purchases, deposits) from the run's QuickBooks corpus, or ([],[])."""
    try:
        purchases = await pool.fetch(
            "SELECT p.purchase_id, p.txn_date, p.amount_cents, p.created_at, "
            "       v.display_name AS vendor_name "
            "FROM app_quickbooks.purchases p "
            "LEFT JOIN app_quickbooks.vendors v ON v.id = p.vendor_pk "
            "JOIN app_quickbooks.companies c ON c.id = p.company_pk "
            "WHERE c.run_id = $1 ORDER BY p.created_at, p.purchase_id", run_id)
        deposits = await pool.fetch(
            "SELECT d.deposit_id, d.txn_date, d.amount_cents, d.created_at, d.lead, "
            "       d.round_kind "
            "FROM app_quickbooks.deposits d "
            "JOIN app_quickbooks.companies c ON c.id = d.company_pk "
            "WHERE c.run_id = $1 ORDER BY d.created_at, d.deposit_id", run_id)
        return purchases, deposits
    except asyncpg.PostgresError:
        return [], []


async def seed_mercury(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    at: Optional[datetime] = None,
) -> dict[str, int]:
    """Provision the org + 3 accounts + a realistic transaction stream for ``run_id``.

    Idempotent. Returns ``{"accounts": N, "transactions": M}`` (zeros if seeded)."""
    existing = await pool.fetchval(
        "SELECT id FROM app_mercury.organizations WHERE run_id = $1", run_id)
    if existing is not None:
        return {"accounts": 0, "transactions": 0}

    seed_row = await pool.fetchrow(
        "SELECT seed, virtual_now FROM org.runs WHERE id = $1", run_id)
    if seed_row is None:
        raise LookupError(f"no run {run_id}")
    rng = Random(int(seed_row["seed"]) ^ 0x6D72_6379)  # 'mrcy'
    now = at or seed_row["virtual_now"] or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    org_pk = _det_uuid(rng)
    await pool.execute(
        """INSERT INTO app_mercury.organizations
            (id, run_id, base_url, legal_business_name, api_token, webhook_secret, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7)""",
        org_pk, run_id, "https://api.mercury.com/api/v1", LEGAL_BUSINESS_NAME,
        API_TOKEN, WEBHOOK_SECRET, now - timedelta(days=900))

    # Provision the bank accounts.
    accts: dict[str, dict] = {}
    for name, kind, sort_key, routing, base in _ACCOUNTS:
        aid = _det_uuid(rng)
        await pool.execute(
            """INSERT INTO app_mercury.accounts
                (id, org_pk, account_id, name, nickname, account_number, routing_number,
                 status, type, kind, available_balance_cents, current_balance_cents,
                 legal_business_name, dashboard_link, can_receive_transactions, sort_key,
                 created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,'active','mercury',$8,$9,$9,$10,$11,TRUE,$12,$13)""",
            aid, org_pk, aid, name, None, _acct_number(rng), routing, kind, base,
            LEGAL_BUSINESS_NAME,
            f"https://mercury.com/transactions/{aid}", sort_key, now - timedelta(days=900))
        accts[kind] = {"pk": aid, "name": name, "base": base}

    counterparties: dict[str, UUID] = {}

    def _cp(name: str) -> UUID:
        if name not in counterparties:
            counterparties[name] = _det_uuid(rng)
        return counterparties[name]

    purchases, deposits = await _quickbooks_flows(pool, run_id)
    flows: list[dict] = []

    if purchases or deposits:
        for p in purchases:
            amt = int(p["amount_cents"])
            kind = "debitCardTransaction" if amt < 50_000 else "externalTransfer"
            flows.append({
                "amount_cents": -amt,
                "kind": kind,
                "counterparty": p["vendor_name"] or "Vendor",
                "created_at": p["created_at"],
                "memo": f"Payment to {p['vendor_name'] or 'vendor'}",
            })
        for d in deposits:
            lead = (d["lead"] or "").strip() or "Investor"
            flows.append({
                "amount_cents": int(d["amount_cents"]),
                "kind": "incomingDomesticWire",
                "counterparty": lead,
                "created_at": d["created_at"],
                "memo": f"{(d['round_kind'] or 'funding').replace('_', ' ').title()} from {lead}",
            })
    else:
        # Synthetic fallback: a plausible 2-year operating stream.
        day = now - timedelta(days=730)
        while day < now - timedelta(days=1):
            if rng.random() < 0.55:
                vendor = rng.choice(_FALLBACK_VENDORS)
                amt = rng.randint(2_000, 900_000)
                flows.append({
                    "amount_cents": -amt,
                    "kind": "debitCardTransaction" if amt < 50_000 else "externalTransfer",
                    "counterparty": vendor,
                    "created_at": day + timedelta(hours=rng.randint(8, 18)),
                    "memo": f"Payment to {vendor}",
                })
            day += timedelta(days=1)
        for funder, kind in _FALLBACK_FUNDERS:
            flows.append({
                "amount_cents": rng.randint(50_000_000, 400_000_000),
                "kind": "incomingDomesticWire",
                "counterparty": funder,
                "created_at": now - timedelta(days=rng.randint(120, 700)),
                "memo": f"{kind.title()} round from {funder}",
            })

    # Inter-account sweeps (exercise the savings/treasury accounts + transfer kinds).
    sweeps = []
    for _ in range(6):
        d = now - timedelta(days=rng.randint(30, 700))
        amt = rng.randint(1_000_000, 20_000_000)
        sweeps.append((accts["savings"], "internalTransfer", amt, "Sweep to savings", d))
    for _ in range(3):
        d = now - timedelta(days=rng.randint(60, 600))
        amt = rng.randint(50_000_000, 150_000_000)
        sweeps.append((accts["treasury"], "treasuryTransfer", amt, "Treasury allocation", d))

    # Insert checking transactions (newest left pending).
    checking = accts["checking"]
    flows.sort(key=lambda f: f["created_at"])
    checking_sum = checking["base"]
    n_pending = min(3, len(flows))
    inserted = 0
    for i, f in enumerate(flows):
        created = f["created_at"]
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        is_pending = i >= len(flows) - n_pending
        status = "pending" if is_pending else "sent"
        posted = None if is_pending else created + timedelta(days=1)
        edd = created + timedelta(days=1)
        txn_id = _det_uuid(rng)
        await pool.execute(
            """INSERT INTO app_mercury.transactions
                (id, account_pk, txn_id, amount_cents, status, kind, counterparty_id,
                 counterparty_name, counterparty_nickname, note, external_memo,
                 bank_description, dashboard_link, version, created_at, posted_at,
                 estimated_delivery_date, is_historical)
               VALUES ($1,$2,$1,$3,$4,$5,$6,$7,NULL,NULL,$8,$9,$10,1,$11,$12,$13,TRUE)""",
            txn_id, checking["pk"], f["amount_cents"], status, f["kind"],
            _cp(f["counterparty"]), f["counterparty"], f["memo"], f["counterparty"],
            f"https://mercury.com/transactions/{txn_id}", created, posted, edd)
        checking_sum += f["amount_cents"]
        inserted += 1

    # Insert sweep transactions on savings/treasury (credits into them).
    for acct, kind, amt, memo, d in sweeps:
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        txn_id = _det_uuid(rng)
        await pool.execute(
            """INSERT INTO app_mercury.transactions
                (id, account_pk, txn_id, amount_cents, status, kind, counterparty_id,
                 counterparty_name, counterparty_nickname, note, external_memo,
                 bank_description, dashboard_link, version, created_at, posted_at,
                 estimated_delivery_date, is_historical)
               VALUES ($1,$2,$1,$3,'sent',$4,$5,$6,NULL,NULL,$7,$8,$9,1,$10,$10,$10,TRUE)""",
            txn_id, acct["pk"], amt, kind, _cp(checking["name"]), checking["name"],
            memo, memo, f"https://mercury.com/transactions/{txn_id}", d)
        inserted += 1

    # Settle the checking account's balance to the running sum (positive floor).
    if checking_sum <= 0:
        checking_sum = 25_000_000
    await pool.execute(
        "UPDATE app_mercury.accounts SET current_balance_cents = $2, "
        "available_balance_cents = $2 WHERE id = $1", checking["pk"], checking_sum)

    return {"accounts": len(_ACCOUNTS), "transactions": inserted}
