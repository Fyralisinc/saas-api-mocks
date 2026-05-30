#!/usr/bin/env python3
"""02_plan_timeline.py — L1 facts.yaml → L2 build/timeline.yaml.

Purely deterministic. Expands the L1 facts into a single sorted list of
dated events that the L3 thread planner consumes. Re-running with the same
facts.yaml produces byte-identical output.

Event kinds emitted (the L2 vocabulary):
  - company.founded
  - team.create
  - person.hire        (from person.started_at)
  - person.depart      (from person.ended_at — only if set)
  - repo.create        (from repo.created_at)
  - milestone.<kind>   (from milestones_curated; kind is the original)
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
FACTS = ROOT / "facts" / "facts.yaml"
OUT = ROOT / "build" / "timeline.yaml"


def parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s)


def main() -> None:
    if not FACTS.exists():
        raise SystemExit(
            f"missing {FACTS}. Hand-edit facts/facts.yaml.draft "
            f"and copy to facts.yaml first."
        )
    facts = yaml.safe_load(FACTS.read_text())
    events: list[dict] = []

    # 1. Company founding.
    company = facts["company"]
    events.append({
        "date": company["founded"],
        "kind": "company.founded",
        "ref": company["id"],
        "payload": {"name": company["name"], "mission": company["mission"]},
    })

    # 2. Teams (no dates in facts — anchor at company founding).
    for team in facts.get("teams_template", []):
        events.append({
            "date": company["founded"],
            "kind": "team.create",
            "ref": team["id"],
            "payload": team,
        })

    # 3. Repos.
    for repo in facts.get("repos", []):
        if not repo.get("created_at"):
            continue
        events.append({
            "date": repo["created_at"],
            "kind": "repo.create",
            "ref": repo["id"],
            "payload": {k: repo[k] for k in
                        ("name", "description", "language", "stars", "archived")},
        })

    # 4. People: hire + optional depart.
    for person in facts.get("people", []):
        if person.get("started_at"):
            events.append({
                "date": person["started_at"],
                "kind": "person.hire",
                "ref": person["id"],
                "payload": {k: person.get(k) for k in
                            ("github_handle", "full_name", "email",
                             "role", "level", "team")},
            })
        if person.get("ended_at"):
            events.append({
                "date": person["ended_at"],
                "kind": "person.depart",
                "ref": person["id"],
                "payload": {},
            })

    # 5. Curated milestones.
    for ms in facts.get("milestones_curated", []):
        events.append({
            "date": ms["date"],
            "kind": f"milestone.{ms['kind']}",
            "ref": f"milestone:{ms['source'].rsplit('/',1)[-1]}",
            "payload": {"title": ms["title"], "source": ms["source"]},
        })

    events.sort(key=lambda e: (e["date"], e["kind"]))

    timeline = {
        "_meta": {
            "from": str(FACTS.relative_to(ROOT)),
            "count": len(events),
            "first": events[0]["date"] if events else None,
            "last": events[-1]["date"] if events else None,
        },
        "events": events,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as f:
        yaml.safe_dump(timeline, f, sort_keys=False, width=120)
    print(f"wrote {OUT}")
    print(f"  {len(events)} events  span={timeline['_meta']['first']} → {timeline['_meta']['last']}")


if __name__ == "__main__":
    main()
