"""Realistic Carta corpus seeding.

Carta is a NET-NEW Tier-C source: the frozen run has no Carta corpus, so we model
a realistic startup CAP TABLE ourselves (the brief sanctions this, like ramp/brex
projecting QB rows onto a stream). The company on Carta is the **issuer**; we
project the run's existing org onto the four cap-table read collections the Fyralis
flow doc names as its signal set (shareholders, share classes, SAFE notes, option
grants):

  * **STAKEHOLDERS** — one per ``org.people`` row (founders / executives / employees,
    relationship derived from level), plus a synthesized set of seed/Series-A
    INVESTOR stakeholders (entity CORPORATION) and a couple of BOARD_MEMBERs.
  * **SHARE_CLASSES** — Common Stock + Series Seed Preferred + Series A Preferred
    (a typical post-Series-A structure).
  * **OPTION_GRANTS** — one ISO/NSO grant on Common per employee/founder/executive
    stakeholder (the primary stream → a genuine multi-page AIP token walk).
  * **CONVERTIBLE_NOTES** — a handful of seed-round SAFEs held by the investors.

Money is stored as integer CENTS (projected to the decimal-string Money wrapper);
share counts as whole integers (projected to ``{value:"<n>.00"}``). Everything is
deterministic off the run seed. Idempotent: a second call after the issuer row
exists is a no-op.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from random import Random
from typing import Optional
from uuid import UUID, uuid4

import asyncpg

# Seed-stable issuer identity (hand these to the ingest-client / memory).
LEGAL_NAME = "Alpen Labs Inc."
DBA_NAME = "Alpen Labs"
WEBSITE = "https://alpenlabs.com"
ISSUER_ID = "70114"                       # Carta issuer-suite ids are numeric strings
CLIENT_ID = "carta_id_9f2a7c4e1b6d8053"
CLIENT_SECRET = "carta_secret_Qm7Zr4kP9xWtT2sLcV7nR8yB3dF6hJ1kM0pQwXy"
ACCESS_TOKEN = "carta_at_Lp83ZqV7mWtR2sNcK9rB4yD6hF1jM5xQ0wT8uIoP3aS"

# Synthesized institutional investors (VC firms) — CORPORATION stakeholders holding
# preferred shares + SAFEs.
_INVESTORS = [
    "Cascade Ventures", "Northpeak Capital", "Tideline Partners",
    "Granite Seed Fund", "Helix Ventures", "Summit Arc Capital",
]
_BOARD = [("Eleanor Voss", "eleanor.voss@cascadeventures.com"),
          ("Marcus Reyes", "marcus.reyes@northpeak.vc")]


def _uuid(rng: Random) -> str:
    return str(UUID(int=rng.getrandbits(128), version=4))


def _relationship(level: str, idx: int) -> str:
    lvl = (level or "").lower()
    if idx < 2:
        return "FOUNDER"
    if any(k in lvl for k in ("exec", "vp", "chief", "director", "head", "principal")):
        return "EXECUTIVE"
    return "EMPLOYEE"


async def seed_carta(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    at: Optional[datetime] = None,
) -> dict[str, int]:
    """Provision the issuer + stakeholders/share-classes/option-grants/SAFEs.

    Idempotent. Returns ``{"stakeholders":S, "shareClasses":C, "optionGrants":G,
    "convertibleNotes":N}``."""
    existing = await pool.fetchval(
        "SELECT id FROM app_carta.issuers WHERE run_id = $1", run_id)
    if existing is not None:
        return {"stakeholders": 0, "shareClasses": 0, "optionGrants": 0,
                "convertibleNotes": 0}

    seed_row = await pool.fetchrow(
        "SELECT seed, virtual_now FROM org.runs WHERE id = $1", run_id)
    if seed_row is None:
        raise LookupError(f"no run {run_id}")
    rng = Random(int(seed_row["seed"]) ^ 0x63_61_72_74)  # 'cart'
    now = at or seed_row["virtual_now"] or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    incorporated = now - timedelta(days=1100)

    issuer_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_carta.issuers
            (id, run_id, base_url, issuer_id, legal_name, doing_business_as_name,
             website, client_id, client_secret, access_token, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)""",
        issuer_pk, run_id, "https://api.carta.com", ISSUER_ID, LEGAL_NAME, DBA_NAME,
        WEBSITE, CLIENT_ID, CLIENT_SECRET, ACCESS_TOKEN, incorporated)

    # ---- SHARE CLASSES: Common + Seed Preferred + Series A Preferred ----------
    share_classes = [
        ("Common Stock", "CS", "COMMON", 20_000_000, "0.0001", 0, False),
        ("Series Seed Preferred", "SS", "PREFERRED", 4_500_000, "0.0001", 1, False),
        ("Series A Preferred", "PA", "PREFERRED", 6_200_000, "0.0001", 2, False),
    ]
    sc_count = 0
    common_sc_uuid = None
    for i, (name, prefix, sctype, auth_sh, par, sen, pp) in enumerate(share_classes):
        sc_uuid = _uuid(rng)
        if sctype == "COMMON":
            common_sc_uuid = sc_uuid
        await pool.execute(
            """INSERT INTO app_carta.share_classes
                (id, issuer_pk, share_class_id, name, prefix, type, authorized_shares,
                 par_value, currency_code, seniority, pari_passu, sort_key, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'USD',$9,$10,$11,$12)""",
            uuid4(), issuer_pk, f"600{i + 1}", name, prefix, sctype, auth_sh, par,
            sen, pp, i, incorporated)
        sc_count += 1

    # ---- STAKEHOLDERS: people -> founders/execs/employees --------------------
    people = await pool.fetch(
        "SELECT handle, full_name, email, role, level, started_at FROM org.people "
        "WHERE run_id = $1 ORDER BY started_at, handle", run_id)
    # (stakeholder_id, full_name, relationship, started_at, employee_idx)
    holders: list[tuple] = []
    sort = 0
    emp_idx = 0
    for i, p in enumerate(people):
        full = (p["full_name"] or p["handle"] or "Stakeholder").strip()
        rel = _relationship(p["level"], i)
        started = p["started_at"] or (now - timedelta(days=700))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        sid = f"{50000 + i}"
        await pool.execute(
            """INSERT INTO app_carta.stakeholders
                (id, issuer_pk, stakeholder_id, full_name, email, employee_id,
                 relationship, entity_type, country, sort_key, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,'INDIVIDUAL','US',$8,$9)""",
            uuid4(), issuer_pk, sid, full, p["email"] or "",
            f"E{1000 + i}", rel, sort, started)
        holders.append((sid, full, rel, started, emp_idx))
        emp_idx += 1
        sort += 1

    # synthesized institutional INVESTORS (CORPORATION) + BOARD members
    investor_ids: list[tuple] = []  # (stakeholder_id, name)
    for j, firm in enumerate(_INVESTORS):
        sid = f"{60000 + j}"
        await pool.execute(
            """INSERT INTO app_carta.stakeholders
                (id, issuer_pk, stakeholder_id, full_name, email, relationship,
                 entity_type, grp, country, sort_key, created_at)
               VALUES ($1,$2,$3,$4,$5,'INVESTOR','CORPORATION','Institutional','US',$6,$7)""",
            uuid4(), issuer_pk, sid, firm,
            f"contact@{firm.split()[0].lower()}.vc", sort, incorporated + timedelta(days=30))
        investor_ids.append((sid, firm))
        sort += 1
    for k, (name, email) in enumerate(_BOARD):
        sid = f"{61000 + k}"
        await pool.execute(
            """INSERT INTO app_carta.stakeholders
                (id, issuer_pk, stakeholder_id, full_name, email, relationship,
                 entity_type, country, sort_key, created_at)
               VALUES ($1,$2,$3,$4,$5,'BOARD_MEMBER','INDIVIDUAL','US',$6,$7)""",
            uuid4(), issuer_pk, sid, name, email, sort,
            incorporated + timedelta(days=60))
        sort += 1
    n_stakeholders = len(holders) + len(investor_ids) + len(_BOARD)

    # ---- OPTION GRANTS: one ISO/NSO on Common per employee/founder/exec -------
    grant_count = 0
    gsort = 0
    for (sid, full, rel, started, _ei) in holders:
        opt_type = "NSO" if rel in ("FOUNDER", "EXECUTIVE") and gsort % 3 == 0 else "ISO"
        qty = rng.choice([5_000, 8_000, 12_000, 20_000, 40_000, 80_000])
        if rel == "FOUNDER":
            qty = rng.choice([400_000, 600_000, 800_000])
        elif rel == "EXECUTIVE":
            qty = rng.choice([80_000, 120_000, 160_000])
        # 4-year monthly vest, 1-year cliff; vested fraction by elapsed months.
        months_elapsed = max(0, int((now - started).days / 30))
        vested = 0 if months_elapsed < 12 else min(qty, int(qty * min(months_elapsed, 48) / 48))
        strike = rng.choice(["0.32", "0.71", "1.05", "1.48", "2.10"])
        issue = started + timedelta(days=rng.randint(14, 75))
        if issue > now:
            issue = started
        last_mod = issue + timedelta(days=rng.randint(0, 400))
        if last_mod > now:
            last_mod = now
        await pool.execute(
            """INSERT INTO app_carta.option_grants
                (id, issuer_pk, grant_id, security_id, share_class_id, stakeholder_id,
                 plan_name, stock_option_type, quantity, vested_quantity,
                 exercised_quantity, exercise_price, currency_code, early_exercisable,
                 issue_date, vesting_start_date, grant_expiration_date, last_modified,
                 sort_key, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,0,$11,'USD',$12,$13,$14,$15,$16,
                       $17,$18)""",
            uuid4(), issuer_pk, f"{25000 + gsort}", _uuid(rng), common_sc_uuid, sid,
            "2023 Equity Incentive Plan", opt_type, qty, vested, strike,
            (rel == "FOUNDER"), issue.date(), issue.date(),
            (issue + timedelta(days=3650)).date(), last_mod, gsort, issue)
        grant_count += 1
        gsort += 1

    # ---- CONVERTIBLE NOTES (SAFEs): seed-round, held by investors ------------
    note_count = 0
    nsort = 0
    n_safes = min(len(investor_ids) + 2, 8) if investor_ids else 0
    for n in range(n_safes):
        sid, firm = investor_ids[n % len(investor_ids)]
        principal_cents = rng.choice([100_000, 150_000, 250_000, 400_000, 500_000]) * 100
        cap_cents = rng.choice([8_000_000, 10_000_000, 12_000_000, 15_000_000]) * 100
        issued = incorporated + timedelta(days=rng.randint(20, 200) + n * 7)
        last_mod = issued + timedelta(days=rng.randint(0, 300))
        if last_mod > now:
            last_mod = now
        await pool.execute(
            """INSERT INTO app_carta.convertible_notes
                (id, issuer_pk, note_id, security_id, stakeholder_id, security_label,
                 cash_paid_cents, price_cap_cents, currency_code, interest_rate,
                 discount_percentage, interest_compounding_period, day_count_basis,
                 issue_datetime, maturity_datetime, last_modified, sort_key, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'USD','0.00','20.00','ANNUALLY',
                       'COUNT_ACTUAL_365',$9,$10,$11,$12,$13)""",
            uuid4(), issuer_pk, f"{8000 + nsort}", _uuid(rng), sid,
            f"SAFE-{nsort + 1:04d}", principal_cents, cap_cents, issued,
            issued + timedelta(days=730), last_mod, nsort, issued)
        note_count += 1
        nsort += 1

    return {"stakeholders": n_stakeholders, "shareClasses": sc_count,
            "optionGrants": grant_count, "convertibleNotes": note_count}
