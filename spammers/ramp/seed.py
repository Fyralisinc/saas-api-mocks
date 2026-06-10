"""Realistic Ramp corpus seeding.

Ramp is a NET-NEW Tier-C source: the frozen run has no Ramp corpus, so we model
realistic content ourselves (the brief sanctions this, like brex projecting QB
purchases onto a card stream). Ramp is the company's **corporate-card + spend**
layer, so we project the run's existing data onto:

  * **USERS** — one Ramp user (employee/cardholder) per ``org.people`` row, with a
    handful flagged as managers.
  * **CARDS** — one virtual/physical card per active user.
  * **TRANSACTIONS** — the run's ``app_quickbooks`` vendor **purchases** become
    settled card transactions (CLEARED, amount POSITIVE = spend), each attributed
    to a random cardholder + card; a few are DECLINED. This is the primary stream
    (the bulk → a genuine multi-page keyset cursor walk).
  * **REIMBURSEMENTS** — a synthesized stream of employee out-of-pocket expense
    requests (OUT_OF_POCKET / MILEAGE / PER_DIEM), attributed to users.

Money is stored as integer CENTS; the wire projects BOTH the top-level dollar
``amount`` and the nested ``CurrencyAmount`` cents. Everything is deterministic
off the run seed. If no QuickBooks corpus is present the transaction stream is
synthesized. Idempotent: a second call after the org row exists is a no-op.
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
BUSINESS_ID = "11111111-2222-4333-8444-555566667777"
CLIENT_ID = "ramp_id_5f3a9c2e7b1d4068"
CLIENT_SECRET = "ramp_secret_aGmZ4kP9xWqT2sLcV7nR8yB3dF6hJ1kM0pQ"
ACCESS_TOKEN = "ramp_business_tok_Hk39ZpQ7mVtR2sLcV7nR8yB3dF6hJ1kM0pQwXyZ"
WEBHOOK_SECRET = "rwhs_3f9a2c8e1b7d4506a9c2e8f1b7d45063"

_ALNUM = string.ascii_lowercase + string.digits
_FALLBACK_VENDORS = [
    ("Amazon Web Services", "5734"), ("Google Cloud", "7372"), ("GitHub", "5734"),
    ("Vercel", "5734"), ("Datadog", "7372"), ("Notion Labs", "5734"),
    ("Linear", "5734"), ("WeWork", "6513"), ("Gusto", "7372"), ("Slack", "5734"),
    ("Figma", "5734"), ("OpenAI", "7372"),
]
_SK_CATEGORIES = {
    "5734": (33, "Computer Software"), "7372": (34, "Cloud Computing & SaaS"),
    "6513": (12, "Office Rent"),
}
_REIMB_TYPES = ["OUT_OF_POCKET", "OUT_OF_POCKET", "MILEAGE", "PER_DIEM"]
_REIMB_MERCHANTS = ["United Airlines", "Lyft", "Marriott", "Starbucks", "Office Depot",
                    "Uber", "Delta Air Lines", "Whole Foods"]


def _uuid(rng: Random) -> str:
    return str(UUID(int=rng.getrandbits(128), version=4))


async def _quickbooks_purchases(pool: asyncpg.Pool, run_id: UUID):
    """Return the run's QuickBooks purchases (vendor card spend), or []."""
    try:
        return await pool.fetch(
            "SELECT p.purchase_id, p.txn_date, p.amount_cents, p.created_at, "
            "       v.display_name AS vendor_name "
            "FROM app_quickbooks.purchases p "
            "LEFT JOIN app_quickbooks.vendors v ON v.id = p.vendor_pk "
            "JOIN app_quickbooks.companies c ON c.id = p.company_pk "
            "WHERE c.run_id = $1 ORDER BY p.created_at, p.purchase_id", run_id)
    except asyncpg.PostgresError:
        return []


async def seed_ramp(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    at: Optional[datetime] = None,
) -> dict[str, int]:
    """Provision the org + users/cards + transaction/reimbursement streams.

    Idempotent. Returns ``{"users":U, "cards":C, "transactions":T, "reimbursements":R}``."""
    existing = await pool.fetchval(
        "SELECT id FROM app_ramp.organizations WHERE run_id = $1", run_id)
    if existing is not None:
        return {"users": 0, "cards": 0, "transactions": 0, "reimbursements": 0}

    seed_row = await pool.fetchrow(
        "SELECT seed, virtual_now FROM org.runs WHERE id = $1", run_id)
    if seed_row is None:
        raise LookupError(f"no run {run_id}")
    rng = Random(int(seed_row["seed"]) ^ 0x72_61_6d_70)  # 'ramp'
    now = at or seed_row["virtual_now"] or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    org_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_ramp.organizations
            (id, run_id, base_url, legal_business_name, business_id, client_id,
             client_secret, access_token, webhook_secret, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
        org_pk, run_id, "https://api.ramp.com", LEGAL_BUSINESS_NAME, BUSINESS_ID,
        CLIENT_ID, CLIENT_SECRET, ACCESS_TOKEN, WEBHOOK_SECRET, now - timedelta(days=900))

    # ---- USERS: one per org.people row ---------------------------------------
    people = await pool.fetch(
        "SELECT handle, full_name, email, role, started_at FROM org.people "
        "WHERE run_id = $1 ORDER BY started_at, handle", run_id)
    users: list[tuple] = []  # (user_id, card_id, full_name, email)
    for i, p in enumerate(people):
        uid = _uuid(rng)
        full = (p["full_name"] or p["handle"] or "User").strip()
        first, _, last = full.partition(" ")
        is_mgr = (i % 7 == 0)
        started = p["started_at"] or (now - timedelta(days=600))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        await pool.execute(
            """INSERT INTO app_ramp.users
                (id, org_pk, user_id, first_name, last_name, email, role, status,
                 department_name, manager_id, is_manager, employee_id, business_id,
                 sort_key, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,'USER_ACTIVE',$8,NULL,$9,$10,$11,$12,$13)""",
            uuid4(), org_pk, uid, first, last or "", p["email"] or "",
            "BUSINESS_ADMIN" if is_mgr else "BUSINESS_USER", "Engineering", is_mgr,
            f"E{1000 + i}", BUSINESS_ID, i, started)
        # one card per user
        card_id = _uuid(rng)
        await pool.execute(
            """INSERT INTO app_ramp.cards
                (id, org_pk, card_id, display_name, last_four, cardholder_id,
                 cardholder_name, entity_id, expiration, is_physical, state, sort_key,
                 created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,NULL,$8,$9,'ACTIVE',$10,$11)""",
            uuid4(), org_pk, card_id, f"{full} Card",
            "".join(str(rng.randint(0, 9)) for _ in range(4)), uid, full,
            f"{rng.randint(1,12):02d}-{(now.year + 3) % 100:02d}",
            (i % 3 == 0), i, started)
        users.append((uid, card_id, full, p["email"] or ""))

    if not users:  # synthetic fallback so the streams still attribute
        for i in range(5):
            uid, card_id = _uuid(rng), _uuid(rng)
            users.append((uid, card_id, f"Employee {i}", f"emp{i}@example.com"))

    # ---- TRANSACTIONS: QB purchases -> CLEARED card spend --------------------
    purchases = await _quickbooks_purchases(pool, run_id)
    txn_rows: list[tuple] = []  # (amount_cents, vendor, mcc, created)
    if purchases:
        for p in purchases:
            vendor = p["vendor_name"] or "Vendor"
            mcc = next((m for v, m in _FALLBACK_VENDORS if v == vendor), "5734")
            txn_rows.append((abs(int(p["amount_cents"])), vendor, mcc, p["created_at"]))
    else:
        day = now - timedelta(days=540)
        while day < now - timedelta(days=1):
            if rng.random() < 0.7:
                vendor, mcc = rng.choice(_FALLBACK_VENDORS)
                txn_rows.append((rng.randint(2_000, 600_000), vendor, mcc,
                                 day + timedelta(hours=rng.randint(8, 19))))
            day += timedelta(days=1)
    txn_rows.sort(key=lambda r: r[3])

    txn_count = 0
    for i, (amt, vendor, mcc, created) in enumerate(txn_rows):
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        settled = created + timedelta(days=1)
        uid, card_id, full, _email = rng.choice(users)
        declined = (rng.random() < 0.02)
        sk_id, sk_name = _SK_CATEGORIES.get(mcc, (None, None))
        await pool.execute(
            """INSERT INTO app_ramp.transactions
                (id, org_pk, txn_id, amount_cents, currency_code, state, sync_status,
                 card_id, card_present, user_id, cardholder_name, merchant_id,
                 merchant_name, merchant_category_code, sk_category_id, sk_category_name,
                 memo, user_transaction_time, accounting_date, settlement_date, synced_at,
                 sort_key, is_historical)
               VALUES ($1,$2,$3,$4,'USD',$5,$6,$7,FALSE,$8,$9,$10,$11,$12,$13,$14,$15,
                       $16,$17,$18,$19,$20,TRUE)""",
            uuid4(), org_pk, _uuid(rng), amt,
            "DECLINED" if declined else "CLEARED",
            "NOT_SYNC_READY" if declined else "SYNCED",
            card_id, uid, full, _uuid(rng), vendor, mcc, sk_id, sk_name,
            None, created, None if declined else settled,
            settled, None if declined else settled + timedelta(days=2), i)
        txn_count += 1

    # ---- REIMBURSEMENTS: synthesized employee out-of-pocket stream -----------
    reimb_count = 0
    n_reimb = max(24, min(80, txn_count // 14)) if txn_count else 24
    for i in range(n_reimb):
        uid, _card, full, email = rng.choice(users)
        cents = rng.randint(1_500, 120_000)
        rtype = rng.choice(_REIMB_TYPES)
        created = now - timedelta(days=rng.randint(2, 500), hours=rng.randint(0, 23))
        submitted = created + timedelta(hours=rng.randint(1, 48))
        approved = submitted + timedelta(days=rng.randint(1, 5))
        synced = approved + timedelta(days=rng.randint(1, 3))
        await pool.execute(
            """INSERT INTO app_ramp.reimbursements
                (id, org_pk, reimb_id, amount_cents, currency, state, type, direction,
                 user_id, user_email, user_full_name, merchant, merchant_id,
                 transaction_date, sync_status, memo, created_at, updated_at,
                 submitted_at, approved_at, synced_at, sort_key, is_historical)
               VALUES ($1,$2,$3,$4,'USD','REIMBURSED',$5,'BUSINESS_TO_USER',$6,$7,$8,$9,
                       $10,$11,'SYNCED',$12,$13,$14,$15,$16,$17,$18,TRUE)""",
            uuid4(), org_pk, _uuid(rng), cents, rtype, uid, email, full,
            rng.choice(_REIMB_MERCHANTS), _uuid(rng), created.date(),
            f"{rtype.replace('_', ' ').title()} expense", created, synced,
            submitted, approved, synced, i)
        reimb_count += 1

    return {"users": len(users), "cards": len(users),
            "transactions": txn_count, "reimbursements": reimb_count}
