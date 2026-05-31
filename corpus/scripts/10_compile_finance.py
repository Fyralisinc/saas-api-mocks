"""10_compile_finance.py — generate QuickBooks events from finance.yaml.

Reads facts.yaml + people.enriched.yaml + office_life.yaml + finance.yaml and
expands the finance schedule into a deterministic stream of QuickBooks events
the renderer can fold into events.jsonl:

  quickbooks.company.create         (1x at company founding)
  quickbooks.account.create         (1 per chart-of-accounts line)
  quickbooks.vendor.create          (1 per vendor referenced anywhere)
  quickbooks.employee.create        (1 per person, on their started_at)
  quickbooks.deposit                (1 per funding round)
  quickbooks.purchase               (monthly payroll + recurring + one-off opex)
  quickbooks.journal.entry          (round-close legal accruals, etc.)

Each event has provider="quickbooks" + a kind, and a payload shaped like the
QuickBooks Online API would emit it (Account, Vendor, Employee, Deposit,
Purchase, JournalEntry resources). The corpus replay layer maps these into
the `app_quickbooks.*` provider tables.

Salary lookup precedence: cofounder rate → (location_bucket, role+level) →
(location_bucket, default). Location buckets are derived from each person's
`location` string in facts.yaml; blank locations default to 'nepal' (the
majority of the team).

Exposed as a module: `from corpus.scripts.compile_finance import quickbooks_events`.
"""
from __future__ import annotations

import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterator

import yaml

ROOT = Path(__file__).resolve().parent.parent
FACTS_PATH    = ROOT / "facts" / "facts.yaml"
OFFICE_PATH   = ROOT / "facts" / "office_life.yaml"
FINANCE_PATH  = ROOT / "facts" / "finance.yaml"


# ---------------------------------------------------------------------------
# Loaders + helpers
# ---------------------------------------------------------------------------

def _yaml(p: Path) -> dict:
    return yaml.safe_load(p.read_text()) or {}


def _iso(d: date, hour: int = 10, minute: int = 0) -> str:
    return f"{d.isoformat()}T{hour:02d}:{minute:02d}:00Z"


def _months_between(start: date, end: date) -> Iterator[date]:
    """Yield the 1st of each month between start and end (inclusive)."""
    y, m = start.year, start.month
    last_y, last_m = end.year, end.month
    while (y, m) <= (last_y, last_m):
        yield date(y, m, 1)
        m += 1
        if m == 13:
            y += 1; m = 1


def _location_bucket(loc: str) -> str:
    """Map a person.location string to one of {nepal, us, eu, other}."""
    if not loc:
        return "nepal"  # team is majority Nepal-based; blank defaults there
    s = loc.lower()
    if any(k in s for k in ("nepal", "kathmandu", "lalitpur", "pokhara")):
        return "nepal"
    if any(k in s for k in ("boston", "nyc", "new york", "america", "us", "san francisco", "seattle")):
        return "us"
    if any(k in s for k in ("istanbul", "İstanbul", "turkey", "berlin", "london", "paris", "estonia", "uk")):
        return "eu"
    return "other"


def _salary_for(person: dict, salary_rules: dict) -> int:
    """Look up annual USD salary for one person from the rules table."""
    # Cofounders short-circuit to the cofounder rate
    if (person.get("role") or "").lower() == "cofounder":
        return int(salary_rules["cofounder"]["any"])

    bucket = _location_bucket(person.get("location", "") or "")
    by_loc = salary_rules["by_location"].get(bucket) or salary_rules["by_location"]["nepal"]

    role = (person.get("role") or "").lower()
    level = (person.get("level") or "ic").lower()
    key = f"{role}_{level}"
    if key in by_loc:
        return int(by_loc[key])
    return int(by_loc.get("default", 70000))


def _schedule_lookup(schedule: list[list[Any]], when: date) -> int:
    """Pick the monthly_usd entry whose effective_from is the latest <= when."""
    best = 0
    for entry in schedule:
        eff_from = date.fromisoformat(str(entry[0]))
        if eff_from <= when:
            best = int(entry[1])
    return best


def _seed_id(prefix: str, *parts: Any) -> str:
    """Deterministic, stable ID for QuickBooks resources."""
    import hashlib
    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return f"{prefix}-{h[:12]}"


# ---------------------------------------------------------------------------
# Bootstrap events: company + chart of accounts + vendors + employees
# ---------------------------------------------------------------------------

def bootstrap_events(facts: dict, finance: dict) -> Iterator[dict]:
    founded = date.fromisoformat(facts["company"]["founded"])

    # The QuickBooks Online "Company" resource — single tenant
    yield {
        "t": _iso(founded, hour=9, minute=0),
        "provider": "quickbooks", "kind": "company.create",
        "payload": {
            "id": "company:alpen-labs",
            "realm_id": "9341453412700001",
            "company_name": facts["company"]["name"],
            "legal_name":   "Alpen Labs, Inc.",
            "country":      "US",
            "fiscal_year_start": "January",
            "currency":     "USD",
        },
    }

    # Chart of accounts
    for idx, a in enumerate(finance["accounts"]):
        yield {
            "t": _iso(founded, hour=9, minute=5 + idx % 50),
            "provider": "quickbooks", "kind": "account.create",
            "payload": {
                "id":          _seed_id("acct", a["number"], a["name"]),
                "number":      a["number"],
                "name":        a["name"],
                "type":        a["type"],
                "subtype":     a["subtype"],
                "description": a["description"],
                "currency":    "USD",
            },
        }

    # Vendors — every distinct vendor referenced in opex_recurring + one_off_rules
    vendor_names: set[str] = set()
    for line in finance.get("opex_recurring", []):
        if line.get("vendor"): vendor_names.add(line["vendor"])
    for rule in (finance.get("one_off_rules") or {}).values():
        if isinstance(rule, dict) and rule.get("vendor"):
            vendor_names.add(rule["vendor"])
    for v in sorted(vendor_names):
        yield {
            "t": _iso(founded, hour=9, minute=20),
            "provider": "quickbooks", "kind": "vendor.create",
            "payload": {
                "id":            _seed_id("vendor", v),
                "display_name":  v,
                "active":        True,
                "currency":      "USD",
            },
        }


def employee_events(facts: dict, finance: dict) -> Iterator[dict]:
    """One employee.create per person, dated to their started_at."""
    for p in facts["people"]:
        started_at = p.get("started_at")
        if not started_at:
            continue
        ann_salary = _salary_for(p, finance["salary_rules"])
        yield {
            "t": _iso(date.fromisoformat(str(started_at)[:10]), hour=10),
            "provider": "quickbooks", "kind": "employee.create",
            "actor": p["id"],
            "payload": {
                "id":             _seed_id("emp", p["id"]),
                "person_id":      p["id"],
                "display_name":   p.get("full_name") or p.get("github_handle"),
                "email":          p.get("email"),
                "title":          p.get("title") or _title_for(p),
                "team":           (p.get("team") or "").removeprefix("team:"),
                "location_bucket": _location_bucket(p.get("location") or ""),
                "annual_salary_usd": ann_salary,
                "active":         True,
                "hired_at":       str(started_at)[:10],
                "released_at":    str(p.get("ended_at"))[:10] if p.get("ended_at") else None,
            },
        }


def _title_for(p: dict) -> str:
    role = (p.get("role") or "engineer").capitalize()
    level = (p.get("level") or "").lower()
    if level == "senior":
        return f"Senior {role}"
    return f"{role}"


# ---------------------------------------------------------------------------
# Income: funding rounds → Deposit
# ---------------------------------------------------------------------------

def deposit_events(finance: dict) -> Iterator[dict]:
    for rnd in finance["funding_rounds"]:
        d = date.fromisoformat(str(rnd["date"]))
        amount = int(rnd["amount_usd"])
        is_grant = rnd.get("kind") == "grant"
        credit_acct_number = "4100" if is_grant else "3000"  # Grant Income / Paid-In Capital
        yield {
            "t": _iso(d, hour=11, minute=0),
            "provider": "quickbooks", "kind": "deposit",
            "payload": {
                "id":             _seed_id("dep", rnd["id"]),
                "round_id":       rnd["id"],
                "round_kind":     rnd["kind"],
                "txn_date":       d.isoformat(),
                "amount_usd":     amount,
                "deposit_to_account": "1000",        # Operating Bank
                "credit_account":     credit_acct_number,
                "lead":           rnd.get("lead", ""),
                "participants":   rnd.get("participants", []),
                "memo":           f"{rnd.get('kind', 'round').upper()} — {rnd.get('note', '')}",
            },
        }


# ---------------------------------------------------------------------------
# Monthly payroll
# ---------------------------------------------------------------------------

def payroll_events(facts: dict, finance: dict, start: date, end: date) -> Iterator[dict]:
    """Per employee per month while active, emit one Purchase entry on the
    25th (typical US semi-monthly payroll cycle: 10th + 25th — we collapse
    to one entry/month for legibility) charged to Payroll Expense."""
    people = facts["people"]

    for month_start in _months_between(start, end):
        pay_date = month_start.replace(day=25)
        for p in people:
            started = _safe_date(p.get("started_at"))
            ended   = _safe_date(p.get("ended_at"))
            if started is None or started > pay_date:
                continue
            if ended is not None and ended < pay_date:
                continue
            ann_salary = _salary_for(p, finance["salary_rules"])
            monthly = ann_salary // 12
            yield {
                "t": _iso(pay_date, hour=15, minute=30),
                "provider": "quickbooks", "kind": "purchase",
                "actor": p["id"],
                "payload": {
                    "id":          _seed_id("pay", p["id"], pay_date.isoformat()),
                    "txn_date":    pay_date.isoformat(),
                    "vendor":      p.get("full_name") or p.get("github_handle"),
                    "vendor_id":   _seed_id("emp", p["id"]),
                    "amount_usd":  monthly,
                    "expense_account": "5000",          # Payroll Expense
                    "payment_account": "1000",          # Operating Bank
                    "category":    "payroll",
                    "memo":        f"Monthly salary — {p.get('full_name', p.get('github_handle'))} "
                                  f"({(p.get('title') or _title_for(p))}, {p.get('team', '').removeprefix('team:')})",
                    "person_id":   p["id"],
                },
            }


def _safe_date(s: Any) -> date | None:
    if s is None: return None
    try:    return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError): return None


# ---------------------------------------------------------------------------
# Recurring opex
# ---------------------------------------------------------------------------

_ACCT_NUM = None
def _account_number(finance: dict, account_name: str) -> str:
    global _ACCT_NUM
    if _ACCT_NUM is None:
        _ACCT_NUM = {a["name"]: a["number"] for a in finance["accounts"]}
    return _ACCT_NUM.get(account_name, "0000")


def recurring_opex_events(finance: dict, start: date, end: date) -> Iterator[dict]:
    for line in finance.get("opex_recurring", []):
        sched = line.get("schedule", [])
        if not sched: continue
        first_effective = date.fromisoformat(str(sched[0][0]))
        for month_start in _months_between(max(start, first_effective), end):
            amount = _schedule_lookup(sched, month_start)
            if amount <= 0: continue
            pay_date = month_start.replace(day=5)
            yield {
                "t": _iso(pay_date, hour=12, minute=15),
                "provider": "quickbooks", "kind": "purchase",
                "payload": {
                    "id":          _seed_id("opex", line["vendor"], line["category"], month_start.isoformat()),
                    "txn_date":    pay_date.isoformat(),
                    "vendor":      line["vendor"],
                    "vendor_id":   _seed_id("vendor", line["vendor"]),
                    "amount_usd":  amount,
                    "expense_account": _account_number(finance, line["account"]),
                    "payment_account": "1000",
                    "category":    line["category"],
                    "memo":        line.get("memo", ""),
                },
            }


# ---------------------------------------------------------------------------
# One-off opex
# ---------------------------------------------------------------------------

def conference_purchase_events(finance: dict, office: dict) -> Iterator[dict]:
    """For each conference in office_life, charge per-attendee travel cost."""
    rule = finance["one_off_rules"]["conference"]
    cost_each = int(rule["cost_per_attendee_usd"])
    for ev in office.get("external_events", []):
        if ev.get("kind") != "conference": continue
        label = ev["label"]
        attendees = (office.get("conference_travel") or {}).get(label, []) or []
        if not attendees: continue
        d = date.fromisoformat(str(ev["date"]))
        amount = cost_each * len(attendees)
        yield {
            "t": _iso(d - timedelta(days=14), hour=13, minute=0),  # booked 2 weeks ahead
            "provider": "quickbooks", "kind": "purchase",
            "payload": {
                "id":          _seed_id("conf", label, ev["date"]),
                "txn_date":    (d - timedelta(days=14)).isoformat(),
                "vendor":      rule["vendor"],
                "vendor_id":   _seed_id("vendor", rule["vendor"]),
                "amount_usd":  amount,
                "expense_account": _account_number(finance, rule["account"]),
                "payment_account": "1000",
                "category":    "travel",
                "memo":        rule["memo_template"].format(
                    conference_label=label, attendee_count=len(attendees)),
                "attendees":   attendees,
                "conference":  label,
            },
        }


def offsite_events(finance: dict, start: date, end: date) -> Iterator[dict]:
    rule = finance["one_off_rules"]["team_offsite"]
    starting_from = date.fromisoformat(str(rule["starting_from"]) + "-01")
    for month_start in _months_between(max(start, starting_from), end):
        if month_start.month not in rule["months"]: continue
        d = month_start.replace(day=15)
        season = "summer" if month_start.month == 6 else "winter"
        yield {
            "t": _iso(d, hour=14, minute=0),
            "provider": "quickbooks", "kind": "purchase",
            "payload": {
                "id":          _seed_id("offsite", d.isoformat()),
                "txn_date":    d.isoformat(),
                "vendor":      rule["vendor"],
                "vendor_id":   _seed_id("vendor", rule["vendor"]),
                "amount_usd":  int(rule["cost_usd"]),
                "expense_account": _account_number(finance, rule["account"]),
                "payment_account": "1000",
                "category":    "offsite",
                "memo":        rule["memo_template"].format(season=season, year=month_start.year),
            },
        }


def funding_legal_events(finance: dict) -> Iterator[dict]:
    rule = finance["one_off_rules"]["funding_legal_fee"]
    pct = float(rule["pct_of_round"])
    cap = int(rule["cap_usd"])
    for rnd in finance["funding_rounds"]:
        if rnd.get("kind") not in ("seed", "strategic"): continue
        round_dt = date.fromisoformat(str(rnd["date"]))
        fee = min(cap, int(rnd["amount_usd"] * pct))
        bill_date = round_dt + timedelta(days=21)
        yield {
            "t": _iso(bill_date, hour=11, minute=30),
            "provider": "quickbooks", "kind": "purchase",
            "payload": {
                "id":          _seed_id("legal", rnd["id"]),
                "txn_date":    bill_date.isoformat(),
                "vendor":      rule["vendor"],
                "vendor_id":   _seed_id("vendor", rule["vendor"]),
                "amount_usd":  fee,
                "expense_account": _account_number(finance, rule["account"]),
                "payment_account": "1000",
                "category":    "legal",
                "memo":        rule["memo_template"].format(
                    round_name=rnd.get("kind", "round").title() + " round"),
                "round_id":    rnd["id"],
            },
        }


def recruiter_fee_events(facts: dict, finance: dict) -> Iterator[dict]:
    rule = finance["one_off_rules"]["recruiter_fee"]
    pct = float(rule["pct_of_first_year_salary"])
    for p in facts["people"]:
        if (p.get("role") or "").lower() == "cofounder": continue
        started = _safe_date(p.get("started_at"))
        if started is None: continue
        # Skip the founding-day backfill — companies don't pay recruiter fees
        # to themselves on Jan 1 for the entire pre-existing team.
        if started <= date.fromisoformat("2022-02-01"): continue
        ann = _salary_for(p, finance["salary_rules"])
        fee = int(ann * pct)
        bill_date = (started.replace(day=1) + timedelta(days=32)).replace(day=10)
        yield {
            "t": _iso(bill_date, hour=12, minute=45),
            "provider": "quickbooks", "kind": "purchase",
            "payload": {
                "id":          _seed_id("rec", p["id"]),
                "txn_date":    bill_date.isoformat(),
                "vendor":      rule["vendor"],
                "vendor_id":   _seed_id("vendor", rule["vendor"]),
                "amount_usd":  fee,
                "expense_account": _account_number(finance, rule["account"]),
                "payment_account": "1000",
                "category":    "recruiting",
                "memo":        rule["memo_template"].format(
                    full_name=p.get("full_name") or p.get("github_handle"),
                    role=p.get("role", ""),
                    team=(p.get("team") or "").removeprefix("team:")),
                "person_id":   p["id"],
            },
        }


def audit_events(finance: dict, start: date, end: date) -> Iterator[dict]:
    rule = finance["one_off_rules"]["security_audit"]
    starting_from = date.fromisoformat(str(rule["starting_from"]) + "-01")
    for month_start in _months_between(max(start, starting_from), end):
        if month_start.month not in rule["months"]: continue
        d = month_start.replace(day=20)
        q = (month_start.month - 1) // 3 + 1
        yield {
            "t": _iso(d, hour=13, minute=0),
            "provider": "quickbooks", "kind": "purchase",
            "payload": {
                "id":          _seed_id("audit", d.isoformat()),
                "txn_date":    d.isoformat(),
                "vendor":      rule["vendor"],
                "vendor_id":   _seed_id("vendor", rule["vendor"]),
                "amount_usd":  int(rule["cost_usd"]),
                "expense_account": _account_number(finance, rule["account"]),
                "payment_account": "1000",
                "category":    "audit",
                "memo":        rule["memo_template"].format(quarter=f"Q{q} {month_start.year}"),
            },
        }


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def quickbooks_events(facts: dict, office: dict, start: str, end: str) -> Iterator[dict]:
    """Emit every QuickBooks event for the given window. Caller is responsible
    for loading facts.yaml + office_life.yaml + finance.yaml — we read finance
    ourselves so the renderer doesn't need to know about it."""
    finance = _yaml(FINANCE_PATH)
    start_d = date.fromisoformat(start)
    end_d   = date.fromisoformat(end)

    yield from bootstrap_events(facts, finance)
    yield from employee_events(facts, finance)
    yield from deposit_events(finance)
    yield from payroll_events(facts, finance, start_d, end_d)
    yield from recurring_opex_events(finance, start_d, end_d)
    yield from conference_purchase_events(finance, office)
    yield from offsite_events(finance, start_d, end_d)
    yield from funding_legal_events(finance)
    yield from recruiter_fee_events(facts, finance)
    yield from audit_events(finance, start_d, end_d)


def main() -> None:
    """Standalone: dump a summary of the would-be quickbooks events without
    writing them. Useful for previewing the financial picture."""
    facts  = _yaml(FACTS_PATH)
    office = _yaml(OFFICE_PATH)
    finance = _yaml(FINANCE_PATH)
    start = facts["company"]["founded"]
    end   = "2026-10-15"

    events = list(quickbooks_events(facts, office, start, end))
    by_kind: dict[str, int] = {}
    by_year_in: dict[int, int] = {}
    by_year_out: dict[int, int] = {}
    for ev in events:
        by_kind[ev["kind"]] = by_kind.get(ev["kind"], 0) + 1
        if ev["kind"] == "deposit":
            y = int(ev["t"][:4])
            by_year_in[y] = by_year_in.get(y, 0) + int(ev["payload"]["amount_usd"])
        elif ev["kind"] == "purchase":
            y = int(ev["t"][:4])
            by_year_out[y] = by_year_out.get(y, 0) + int(ev["payload"]["amount_usd"])

    print(f"events: {len(events)}", file=sys.stderr)
    for k, n in sorted(by_kind.items()):
        print(f"  {k:25s} {n:6d}", file=sys.stderr)
    print(file=sys.stderr)
    print(f"{'year':5s} {'cash in':>15s} {'cash out':>15s} {'net':>15s}", file=sys.stderr)
    for y in sorted(set(list(by_year_in) + list(by_year_out))):
        cin, cout = by_year_in.get(y, 0), by_year_out.get(y, 0)
        print(f"{y:5d} {cin:15,d} {cout:15,d} {cin - cout:15,d}", file=sys.stderr)


if __name__ == "__main__":
    main()
