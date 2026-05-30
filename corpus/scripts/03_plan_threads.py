#!/usr/bin/env python3
"""03_plan_threads.py — L2 timeline.yaml → L3 threads/THR-*.yaml.

For each major milestone + each ongoing-work focus area, one LLM call
produces a structured "narrative thread spec": cast, beats, tensions,
artifacts. These specs are what 05_render_events.py expands into the
per-provider events stream.

Outputs:
  threads/THR-001-<slug>.yaml ... (one per generated thread)
  build/threads.index.json    (manifest)

Cost guardrails:
  --dry-run        prints prompts + estimated cost; no API calls
  --budget-usd N   abort if projected cost exceeds N (default 5.00)
  --only THR-id    regenerate just one thread (skip cache for it)
  --model NAME     override (default: deepseek-chat)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _llm import Budget, chat            # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FACTS = ROOT / "facts" / "facts.yaml"
TIMELINE = ROOT / "build" / "timeline.yaml"
THREADS = ROOT / "threads"
INDEX = ROOT / "build" / "threads.index.json"
CACHE = ROOT / "cache" / "threads"


SYSTEM_PROMPT = """You are designing a realistic, week-by-week story arc \
("narrative thread") for one ongoing initiative inside a small Bitcoin/ZK \
protocol startup. You will be given:

  - facts: the company, its products, its people (real GitHub handles)
  - the specific anchor (a milestone or an ongoing-work focus area)
  - the time window

Produce a SINGLE YAML object describing the arc. The arc will later be \
expanded into thousands of Slack messages, Jira tickets, PRs, Notion docs, \
calendar invites — so be concrete: real cast members (by `person:<handle>` \
id, drawn ONLY from the provided people list), specific beats, specific \
artifacts.

The arc must reflect realistic engineering work — natural distributions of:
  - per-person throughput (some fast, some slow, some bursty)
  - cross-team handoff friction (review queues, dependencies that block)
  - plan-vs-actual drift (initial estimate, what actually shipped)
  - direction changes documented in design docs or RFCs

Do NOT label these signals; weave them naturally into beats and tensions.

REQUIRED OUTPUT FORMAT (YAML, no markdown fences, no commentary):

id: THR-NNN-<slug>
title: <human title>
window:
  start: YYYY-MM-DD
  end:   YYYY-MM-DD
driver:    person:<handle>          # one person who owns the arc
cast:                                # 3–8 people involved
  - person:<handle>
linked_anchors:                      # references back into timeline.yaml
  - <anchor id or slug>
linked_products: [product:<id>, ...]
linked_repos:    [repo:<name>, ...]
beats:                                # 6–12 beats; each typically 1–3 weeks
  - id: B1
    start: YYYY-MM-DD
    end:   YYYY-MM-DD
    kind:  kickoff | design | impl | review | audit | ship | postmortem | retro | hiring
    summary: <one sentence>
    participants: [person:<handle>, ...]
    tensions: [<short phrase>, ...]   # 0–3 frictions surfaced this beat
artifacts:                            # high-signal docs to LLM-generate later
  - kind: rfc | postmortem | design_doc | retro | all_hands_recap
    title: <human title>
    beat:  B<n>
    author: person:<handle>
tensions:                             # arc-level tensions (separate from per-beat)
  - <short phrase>
"""


def _facts_summary(facts: dict) -> str:
    """Compact, prompt-cacheable facts blob."""
    people_brief = [
        {"id": p["id"], "handle": p["github_handle"],
         "name": p["full_name"], "role": p["role"], "team": p["team"],
         "level": p["level"], "joined": p["started_at"]}
        for p in facts["people"] if p["full_name"] != "needs:review"
    ]
    teams = [{"id": t["id"], "name": t["name"]}
             for t in facts.get("teams_template", [])]
    products = [{"id": p["id"], "name": p["name"]} for p in facts["products"]]
    repos = [{"id": r["id"], "name": r["name"], "lang": r["language"]}
             for r in facts["repos"] if not r["archived"]]
    return yaml.safe_dump({
        "company": facts["company"]["name"],
        "mission": facts["company"]["mission"],
        "teams": teams,
        "products": products,
        "repos": repos,
        "people": people_brief,
    }, sort_keys=False, width=120, allow_unicode=True)


def _enumerate_anchors(timeline: dict) -> list[dict]:
    """Pick anchors worth generating a thread for.

    - Every curated milestone (kind starts with 'milestone.')
    - Each major active repo (top 6 by repo-create position before the end window)
    """
    anchors = []
    for ev in timeline["events"]:
        if ev["kind"].startswith("milestone."):
            anchors.append({
                "kind": "milestone",
                "id": ev["ref"],
                "date": ev["date"],
                "title": ev["payload"]["title"],
                "window_days": 90,
            })
    # Top repos = most-active-looking by name (curated short list).
    for name in ["alpen", "strata-bridge", "mosaic", "alpen-dashboards",
                 "bitcoind-async-client", "zkaleido"]:
        # Anchor each one to its repo.create date with a 12-month window.
        ev = next((e for e in timeline["events"]
                   if e["kind"] == "repo.create" and e["payload"]["name"] == name),
                  None)
        if ev:
            anchors.append({
                "kind": "ongoing",
                "id": ev["ref"],
                "date": ev["date"],
                "title": f"Ongoing work in {name}",
                "window_days": 365,
            })
    return anchors


def _thread_path(thread_id: str) -> Path:
    THREADS.mkdir(parents=True, exist_ok=True)
    return THREADS / f"{thread_id}.yaml"


def _generate_one(facts_blob: str, anchor: dict, idx: int, budget: Budget,
                  dry_run: bool, model: str) -> dict | None:
    start = anchor["date"]
    end = (datetime.fromisoformat(start)
           + timedelta(days=anchor["window_days"])).date().isoformat()

    user_prompt = f"""Anchor: {anchor['kind']} — {anchor['title']}
Anchor id: {anchor['id']}
Time window: {start} to {end}
Suggested thread id: THR-{idx:03d}-{anchor['id'].split(':')[-1][:20]}

Generate the thread spec.
"""
    messages = [
        # The big facts blob goes first as a SYSTEM-role prefix so DeepSeek's
        # prompt caching can match it across calls.
        {"role": "system", "content": SYSTEM_PROMPT + "\n\nFACTS:\n" + facts_blob},
        {"role": "user", "content": user_prompt},
    ]
    raw = chat(messages, budget=budget, cache_dir=CACHE,
               model=model, temperature=0.6, dry_run=dry_run, max_tokens=3000)
    if dry_run:
        return None
    try:
        spec = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        print(f"  [warn] {anchor['id']}: YAML parse failed: {e}", file=sys.stderr)
        return None
    if not isinstance(spec, dict) or "id" not in spec or "beats" not in spec:
        print(f"  [warn] {anchor['id']}: spec malformed", file=sys.stderr)
        return None
    return spec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--budget-usd", type=float, default=5.0)
    ap.add_argument("--only", default=None,
                    help="only regenerate thread(s) matching this anchor id substring")
    ap.add_argument("--model", default="deepseek-chat")
    args = ap.parse_args()

    facts = yaml.safe_load(FACTS.read_text())
    timeline = yaml.safe_load(TIMELINE.read_text())
    facts_blob = _facts_summary(facts)
    anchors = _enumerate_anchors(timeline)
    if args.only:
        anchors = [a for a in anchors if args.only in a["id"]]
    if not anchors:
        raise SystemExit("no anchors matched")
    print(f"anchors to plan: {len(anchors)}", file=sys.stderr)

    budget = Budget(cap_usd=args.budget_usd)
    index: list[dict] = []

    for i, anchor in enumerate(anchors, start=1):
        print(f"[{i}/{len(anchors)}] {anchor['kind']} {anchor['id']} → "
              f"window={anchor['window_days']}d", file=sys.stderr)
        spec = _generate_one(facts_blob, anchor, i, budget,
                             dry_run=args.dry_run, model=args.model)
        if spec is None:
            continue
        path = _thread_path(spec["id"])
        path.write_text(yaml.safe_dump(spec, sort_keys=False,
                                       width=120, allow_unicode=True))
        index.append({"id": spec["id"], "anchor_id": anchor["id"],
                      "kind": anchor["kind"], "path": str(path.relative_to(ROOT))})
        print(f"  wrote {path.name}  budget={budget.summary()}", file=sys.stderr)

    INDEX.parent.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(index, indent=2))
    print(f"\nDONE  threads={len(index)}  {budget.summary()}", file=sys.stderr)


if __name__ == "__main__":
    main()
