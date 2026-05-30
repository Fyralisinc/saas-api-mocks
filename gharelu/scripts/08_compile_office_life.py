#!/usr/bin/env python3
"""08_compile_office_life.py — the "what's happening around the work" calendar.

Three layers, all deterministic from facts.yaml + handle hashes:

  pto:              per-person vacation/sick windows (people are HUMAN, they leave)
  external_events:  industry/market/security events the team has to react to
                    (Bitcoin congestion, audit firm responses, conference travel)
  social:           team offsites, parties, all-hands (NOT work-content)

Output is read by 05_render_events.py:
  - During someone's PTO: zero events for them, plus 1 "out next week"
    Slack announce a few days before, plus a "back, catching up" on return.
  - External events: spawn a small reactive Slack burst in #random and #incidents,
    optionally a side-quest Jira issue if impact=response_required.
  - Social events: calendar invites + chatter.
"""
from __future__ import annotations

import hashlib
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
FACTS = ROOT / "facts" / "facts.yaml"
OUT = ROOT / "facts" / "office_life.yaml"


def _seed(*parts) -> float:
    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _hash_pick(opts: list, *parts) -> str:
    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return opts[int(h[:8], 16) % len(opts)]


# Curated set — Bitcoin/ZK industry events spread across 2024–2026. Plausible
# things a Bitcoin protocol team would react to in real time. Picked from real
# happenings + the kind of thing that lives in a public crypto calendar.
EXTERNAL_EVENTS = [
    # 2024
    {"date": "2024-04-19", "kind": "halving",       "label": "Bitcoin halving (block 840000)", "impact": "watching", "days": 1},
    {"date": "2024-05-08", "kind": "milestone",     "label": "Strata public announce — alpen blog post", "impact": "social", "days": 2},
    {"date": "2024-07-26", "kind": "conference",    "label": "BTC++ Berlin", "impact": "travel", "days": 5, "travelers_pct": 0.15},
    {"date": "2024-09-12", "kind": "disclosure",    "label": "Bitcoin Core 27.1 security disclosure", "impact": "response_required", "days": 3},
    {"date": "2024-10-04", "kind": "external_press","label": "BitVM2 SNARK verification public post", "impact": "social", "days": 2},
    {"date": "2024-11-28", "kind": "thanksgiving_us","label": "US Thanksgiving slow week", "impact": "low_volume", "days": 4},
    {"date": "2024-12-23", "kind": "winter_break",  "label": "Winter break — most of team off", "impact": "low_volume", "days": 12},
    # 2025
    {"date": "2025-01-09", "kind": "strategic_round","label": "Strategic round announce", "impact": "social", "days": 2},
    {"date": "2025-03-12", "kind": "conference",    "label": "BTC++ Asia", "impact": "travel", "days": 6, "travelers_pct": 0.20},
    {"date": "2025-04-29", "kind": "incident",      "label": "Testnet reorg longer than expected", "impact": "response_required", "days": 4},
    {"date": "2025-06-13", "kind": "incident",      "label": "Upstream dep (bitcoind-async-client) CVE — needs patch", "impact": "response_required", "days": 2},
    {"date": "2025-08-04", "kind": "milestone",     "label": "Alpen public testnet launch", "impact": "high_attention", "days": 3},
    {"date": "2025-08-19", "kind": "milestone",     "label": "Glock release week", "impact": "high_attention", "days": 3},
    {"date": "2025-09-22", "kind": "conference",    "label": "ZK Day (Devcon side-event)", "impact": "travel", "days": 4, "travelers_pct": 0.18},
    {"date": "2025-10-15", "kind": "milestone",     "label": "Starknet-Alpen shared verifier announce", "impact": "social", "days": 2},
    {"date": "2025-11-27", "kind": "thanksgiving_us","label": "US Thanksgiving slow week", "impact": "low_volume", "days": 4},
    {"date": "2025-12-22", "kind": "winter_break",  "label": "Winter break", "impact": "low_volume", "days": 14},
    # 2026
    {"date": "2026-01-05", "kind": "milestone",     "label": "Inside Alpen's 2025 retrospective post", "impact": "social", "days": 2},
    {"date": "2026-03-02", "kind": "milestone",     "label": "BTC Credit Markets paper drop", "impact": "social", "days": 3},
    {"date": "2026-03-09", "kind": "milestone",     "label": "Duty-Free Bits blog post", "impact": "social", "days": 2},
    {"date": "2026-05-07", "kind": "milestone",     "label": "Mosaic public release", "impact": "high_attention", "days": 3},
]


# Per-year vacation budget per person.
def _pto_for_person(person: dict, year: int) -> list[dict]:
    handle = person["github_handle"]
    role = person["role"]
    out = []

    # Winter break — 60% of team takes 5+ days off
    if _seed(handle, year, "winter") < 0.6:
        # Start somewhere between Dec 20–28
        start_offset = int(_seed(handle, year, "winter", "start") * 8)
        dur = 5 + int(_seed(handle, year, "winter", "dur") * 5)
        out.append({"start": f"{year}-12-{20+start_offset:02d}",
                    "end": (date(year, 12, 20+start_offset) + timedelta(days=dur)).isoformat(),
                    "kind": "vacation", "label": "winter break"})

    # Summer vacation — most people take one 7-10 day window in Jun-Aug
    if _seed(handle, year, "summer") < 0.85:
        month = 6 + int(_seed(handle, year, "summer", "month") * 3)
        day = 1 + int(_seed(handle, year, "summer", "day") * 22)
        dur = 5 + int(_seed(handle, year, "summer", "dur") * 8)
        start_d = date(year, month, day)
        out.append({"start": start_d.isoformat(),
                    "end": (start_d + timedelta(days=dur)).isoformat(),
                    "kind": "vacation", "label": "summer pto"})

    # Spring or fall mini-break — 40% take a 3-5 day window
    if _seed(handle, year, "mini") < 0.4:
        # Random week in Mar-May or Sep-Nov
        if _seed(handle, year, "mini", "season") < 0.5:
            month = 3 + int(_seed(handle, year, "mini", "month") * 3)
        else:
            month = 9 + int(_seed(handle, year, "mini", "month") * 3)
        day = 1 + int(_seed(handle, year, "mini", "day") * 25)
        dur = 2 + int(_seed(handle, year, "mini", "dur") * 4)
        start_d = date(year, month, day)
        out.append({"start": start_d.isoformat(),
                    "end": (start_d + timedelta(days=dur)).isoformat(),
                    "kind": "vacation", "label": "long weekend"})

    # 1-2 sick days per year (1 day each)
    sick_count = 1 + int(_seed(handle, year, "sick_n") * 2)
    for i in range(sick_count):
        month = 1 + int(_seed(handle, year, "sick", i, "m") * 12)
        try:
            day = 1 + int(_seed(handle, year, "sick", i, "d") * 27)
            d = date(year, month, day)
            out.append({"start": d.isoformat(), "end": d.isoformat(),
                        "kind": "sick", "label": "sick day"})
        except ValueError:
            pass

    # Cofounders skip mini-breaks but take their winter + summer
    if "founder" in role.lower():
        out = [p for p in out if p["kind"] != "vacation" or p["label"] != "long weekend"]

    return out


def _conference_travelers(person: dict, ev: dict) -> bool:
    """Stable: would this person attend this conference?"""
    if ev.get("travelers_pct") is None:
        return False
    return _seed(person["github_handle"], ev["date"], "travel") < ev["travelers_pct"]


def main() -> None:
    facts = yaml.safe_load(FACTS.read_text())
    people = facts["people"]

    # Years to plan PTO for — based on company founding through near-future
    years = list(range(2023, 2027))

    pto: dict[str, list[dict]] = {}
    for p in people:
        windows: list[dict] = []
        for y in years:
            # Skip years before they joined
            if p.get("started_at") and p["started_at"][:4] > str(y):
                continue
            windows.extend(_pto_for_person(p, y))
        pto[p["id"]] = sorted(windows, key=lambda w: w["start"])

    # Mark which people are traveling for each conference (becomes effective PTO).
    travel = {}
    for ev in EXTERNAL_EVENTS:
        if ev["kind"] != "conference":
            continue
        attendees = [p["id"] for p in people if _conference_travelers(p, ev)]
        if attendees:
            travel[ev["label"]] = attendees

    out = {
        "pto": pto,
        "external_events": EXTERNAL_EVENTS,
        "conference_travel": travel,
    }
    OUT.write_text(yaml.safe_dump(out, sort_keys=False, width=120, allow_unicode=True))

    total_windows = sum(len(v) for v in pto.values())
    print(f"wrote {OUT}", file=sys.stderr)
    print(f"  {len(pto)} people  {total_windows} pto windows  "
          f"{len(EXTERNAL_EVENTS)} external events  "
          f"{sum(len(v) for v in travel.values())} conference travelers", file=sys.stderr)
    # Sample summary
    print("\n  sample windows for first 4 people:", file=sys.stderr)
    for p in people[:4]:
        windows = pto[p["id"]]
        print(f"    {p['github_handle']:18s} ({len(windows)} windows):", file=sys.stderr)
        for w in windows[:3]:
            print(f"      {w['start']} → {w['end']}  {w['label']}", file=sys.stderr)


if __name__ == "__main__":
    main()
