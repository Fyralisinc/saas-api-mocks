"""Realistic Brex corpus seeding.

Brex is a NET-NEW Tier-C source: the frozen run has no Brex corpus, so we model
realistic content ourselves (the brief sanctions this, like mercury projecting QB
finance into a bank stream). Brex is the company's **corporate-card + cash**
layer, so we project the run's existing ``app_quickbooks`` finance rows onto two
Brex accounts:

  * the primary **CARD** account carries the **vendor purchases** as
    ``PURCHASE`` card transactions (positive = a charge) — the corporate-card
    spend stream (the bulk → multi-page cursor). The accounting record of those
    same purchases lives in QuickBooks (Bill/BillPayment); Brex is where the
    charge actually happened.
  * the primary **CASH** account carries the **funding deposits** as positive
    ``PAYMENT`` cash transactions, the monthly Brex **CARD_COLLECTION** (Brex
    auto-debits cash to pay the card balance — a real Brex flow, negative),
    monthly ``INTEREST`` (positive), and a few ``FEE`` (negative).

Money is SIGNED INTEGER CENTS, emitted verbatim (Brex amounts are minor units on
the wire). Everything is deterministic off the run seed. If no QuickBooks corpus
is present the streams are synthesized. Idempotent: a second call after the org
row exists is a no-op.
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
COMPANY_ID = "cmp_alpenlabs0000000000001"
API_TOKEN = "bxt_jBWQLZXtu1f4sVT6UjaWPp7Gh9nVGjzEZgRX"
WEBHOOK_SECRET = "whsec_e/ETGy3XpsXyhQS7MKyfI3wG5wTGkq6MoNQpIWTdIyg="

_ALNUM = string.ascii_lowercase + string.digits
_FALLBACK_VENDORS = [
    ("Amazon Web Services", "5734"), ("Google Cloud", "7372"), ("GitHub", "5734"),
    ("Vercel", "5734"), ("Datadog", "7372"), ("Notion Labs", "5734"),
    ("Linear", "5734"), ("WeWork", "6513"), ("Gusto", "7372"), ("Slack", "5734"),
    ("Figma", "5734"), ("OpenAI", "7372"),
]
_FALLBACK_FUNDERS = [("Paradigm", "grant"), ("a16z crypto", "seed"),
                     ("Electric Capital", "strategic")]


def _opaque(rng: Random, prefix: str, n: int = 22) -> str:
    return prefix + "".join(rng.choice(_ALNUM) for _ in range(n))


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


async def seed_brex(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    at: Optional[datetime] = None,
) -> dict[str, int]:
    """Provision the org + cash/card accounts + transaction streams for ``run_id``.

    Idempotent. Returns ``{"accounts": N, "cash_txns": C, "card_txns": K}``."""
    existing = await pool.fetchval(
        "SELECT id FROM app_brex.organizations WHERE run_id = $1", run_id)
    if existing is not None:
        return {"accounts": 0, "cash_txns": 0, "card_txns": 0}

    seed_row = await pool.fetchrow(
        "SELECT seed, virtual_now FROM org.runs WHERE id = $1", run_id)
    if seed_row is None:
        raise LookupError(f"no run {run_id}")
    rng = Random(int(seed_row["seed"]) ^  0x62_72_65_78)  # 'brex'
    now = at or seed_row["virtual_now"] or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    org_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_brex.organizations
            (id, run_id, base_url, legal_business_name, company_id, api_token,
             webhook_secret, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
        org_pk, run_id, "https://api.brex.com", LEGAL_BUSINESS_NAME, COMPANY_ID,
        API_TOKEN, WEBHOOK_SECRET, now - timedelta(days=900))

    # The primary cash account + the primary card account.
    cash_id = _opaque(rng, "dpsa_")
    card_id_acct = _opaque(rng, "cuvs_")
    cash_pk, card_pk = uuid4(), uuid4()
    cash_acct_number = "".join(str(rng.randint(0, 9)) for _ in range(9))
    stmt_start = now.replace(day=1).date()
    stmt_end = (stmt_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    await pool.execute(
        """INSERT INTO app_brex.accounts
            (id, org_pk, account_id, kind, name, status, account_number,
             routing_number, currency, current_balance_cents, available_balance_cents,
             is_primary, sort_key, created_at)
           VALUES ($1,$2,$3,'cash','Brex Cash','ACTIVE',$4,'121145349','USD',$5,$5,
                   TRUE,0,$6)""",
        cash_pk, org_pk, cash_id, cash_acct_number, 0, now - timedelta(days=900))
    await pool.execute(
        """INSERT INTO app_brex.accounts
            (id, org_pk, account_id, kind, status, currency, current_balance_cents,
             available_balance_cents, account_limit_cents, statement_start, statement_end,
             is_primary, sort_key, created_at)
           VALUES ($1,$2,$3,'card','ACTIVE','USD',$4,$5,$6,$7,$8,TRUE,0,$9)""",
        card_pk, org_pk, card_id_acct, 0, 0, 50_000_000, stmt_start, stmt_end,
        now - timedelta(days=900))

    physical_card = _opaque(rng, "card_")
    purchases, deposits = await _quickbooks_flows(pool, run_id)

    # ---- CARD account: vendor purchases -> PURCHASE card transactions --------
    card_rows = []
    if purchases:
        for p in purchases:
            amt = int(p["amount_cents"])
            vendor = p["vendor_name"] or "Vendor"
            mcc = next((m for v, m in _FALLBACK_VENDORS if v == vendor), "5734")
            card_rows.append((abs(amt), "PURCHASE", vendor, mcc, p["created_at"]))
    else:
        day = now - timedelta(days=540)
        while day < now - timedelta(days=1):
            if rng.random() < 0.7:
                vendor, mcc = rng.choice(_FALLBACK_VENDORS)
                card_rows.append((rng.randint(2_000, 600_000), "PURCHASE", vendor, mcc,
                                  day + timedelta(hours=rng.randint(8, 19))))
            day += timedelta(days=1)
    # A few REFUNDs (negative) sprinkled in.
    for _ in range(min(6, len(card_rows))):
        src = rng.choice(card_rows)
        card_rows.append((-rng.randint(2_000, 40_000), "REFUND", src[2], src[3],
                          src[4] + timedelta(days=rng.randint(2, 20))))

    card_rows.sort(key=lambda r: r[4])
    card_count = 0
    card_current = 0
    for i, (amt, ttype, vendor, mcc, created) in enumerate(card_rows):
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        posted = created + timedelta(days=1)
        await pool.execute(
            """INSERT INTO app_brex.transactions
                (id, account_pk, account_kind, txn_id, description, amount_cents, currency,
                 txn_type, initiated_at, posted_at, card_id, merchant_raw_descriptor,
                 merchant_mcc, merchant_country, expense_id, sort_key, is_historical)
               VALUES ($1,$2,'card',$3,$4,$5,'USD',$6,$7,$8,$9,$10,$11,'USA',$12,$13,TRUE)""",
            uuid4(), card_pk, _opaque(rng, "txn_"), f"{vendor}", amt, ttype,
            created, posted, physical_card, vendor.upper()[:22], mcc,
            _opaque(rng, "expense_"), i, )
        card_current += amt
        card_count += 1

    # ---- CASH account: deposits + monthly card collection + interest/fees ----
    cash_events: list[tuple[int, str, str, datetime, Optional[str]]] = []
    if deposits:
        for d in deposits:
            lead = (d["lead"] or "").strip() or "Investor"
            cash_events.append((int(d["amount_cents"]), "PAYMENT",
                                f"{(d['round_kind'] or 'funding').replace('_', ' ').title()} "
                                f"from {lead}", d["created_at"], _opaque(rng, "trnsfr_")))
    else:
        for funder, kind in _FALLBACK_FUNDERS:
            cash_events.append((rng.randint(50_000_000, 400_000_000), "PAYMENT",
                                f"{kind.title()} round from {funder}",
                                now - timedelta(days=rng.randint(120, 520)),
                                _opaque(rng, "trnsfr_")))
    # Monthly card collection (Brex debits cash to pay the card), interest, a few fees.
    month = (now - timedelta(days=480)).replace(day=2)
    while month < now:
        cash_events.append((-rng.randint(200_000, 3_000_000), "CARD_COLLECTION",
                            "Brex card balance collection", month, _opaque(rng, "trnsfr_")))
        cash_events.append((rng.randint(5_000, 80_000), "INTEREST",
                            "Account interest", month + timedelta(days=1), None))
        month = (month + timedelta(days=32)).replace(day=2)
    for _ in range(4):
        cash_events.append((-rng.randint(1_000, 9_000), "FEE", "Wire fee",
                            now - timedelta(days=rng.randint(30, 460)), None))

    cash_events.sort(key=lambda e: e[3])
    cash_count = 0
    cash_balance = 0
    for i, (amt, ttype, desc, created, transfer_id) in enumerate(cash_events):
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        posted = created + timedelta(days=1)
        await pool.execute(
            """INSERT INTO app_brex.transactions
                (id, account_pk, account_kind, txn_id, description, amount_cents, currency,
                 txn_type, initiated_at, posted_at, transfer_id, sort_key, is_historical)
               VALUES ($1,$2,'cash',$3,$4,$5,'USD',$6,$7,$8,$9,$10,TRUE)""",
            uuid4(), cash_pk, _opaque(rng, "txn_"), desc, amt, ttype, created, posted,
            transfer_id, i)
        cash_balance += amt
        cash_count += 1

    # Settle balances (positive floor).
    if cash_balance <= 0:
        cash_balance = 75_000_000
    await pool.execute(
        "UPDATE app_brex.accounts SET current_balance_cents=$2, available_balance_cents=$2 "
        "WHERE id=$1", cash_pk, cash_balance)
    card_outstanding = max(0, card_current)
    await pool.execute(
        "UPDATE app_brex.accounts SET current_balance_cents=$2, available_balance_cents=$3 "
        "WHERE id=$1", card_pk, card_outstanding, 50_000_000 - card_outstanding)

    return {"accounts": 2, "cash_txns": cash_count, "card_txns": card_count}
