#!/usr/bin/env python3
"""04_generate_artifacts.py — threads/*.yaml → artifacts/*.md (LLM prose).

For each artifact entry in each thread spec, generate the actual document
text. These are the 5–10% of corpus content Fyralis actually *reads* to
understand company state — RFCs, postmortems, design docs, retros.

Reads: threads/THR-*.yaml, facts/facts.yaml
Writes: artifacts/<thread_id>__<artifact_id>.md
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _llm import Budget, chat            # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FACTS = ROOT / "facts" / "facts.yaml"
THREADS = ROOT / "threads"
ARTIFACTS = ROOT / "artifacts"
CACHE = ROOT / "cache" / "artifacts"


KIND_PROMPTS = {
    "rfc": (
        "Write an internal RFC (Request for Comments) document. ~800–1500 words. "
        "Use standard structure: Summary, Motivation, Detailed design, "
        "Drawbacks, Alternatives considered, Open questions. "
        "Be specific to the technical context (Bitcoin/ZK protocol work). "
        "Mention specific people from the cast where natural."
    ),
    "postmortem": (
        "Write a blameless internal postmortem. ~500–1000 words. "
        "Structure: TL;DR, Impact, Timeline (with timestamps), Root cause, "
        "What went well, What went poorly, Action items (with owners from cast). "
        "Concrete numbers and specific commits/PRs where natural."
    ),
    "design_doc": (
        "Write an internal design doc. ~600–1200 words. "
        "Structure: Goal, Non-goals, Background, Proposed design, "
        "Trade-offs, Rollout plan. Realistic Bitcoin/ZK technical depth."
    ),
    "retro": (
        "Write a sprint/quarter retro doc. ~400–700 words. "
        "Sections: Wins, Challenges, What we'd do differently, Action items. "
        "Reference real beats and tensions from the thread."
    ),
    "all_hands_recap": (
        "Write an all-hands recap memo. ~500–800 words. "
        "Sections: Highlights, What shipped, Coming up, Q&A summary, "
        "Shoutouts (to specific cast members)."
    ),
}


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "untitled").lower()).strip("-")
    return s[:60] or "untitled"


def _people_short(facts: dict) -> str:
    return "\n".join(
        f"- {p['id']} ({p['github_handle']}, {p['role']}, {p['team']})"
        for p in facts["people"] if p["full_name"] != "needs:review"
    )


def _generate_one(facts: dict, thread: dict, artifact: dict,
                  budget: Budget, dry_run: bool, model: str) -> str | None:
    kind = artifact.get("kind", "design_doc")
    template = KIND_PROMPTS.get(kind, KIND_PROMPTS["design_doc"])

    sys_prompt = f"""You are writing internal company documents for a small \
Bitcoin/ZK protocol startup (Alpen Labs). Voice: practical, technically dense, \
no marketing fluff. First-person when natural.

{template}

PEOPLE (use these handles exactly — they're real teammates):
{_people_short(facts)}
"""
    # Strip the thread down to what's relevant for context.
    relevant_beat = next(
        (b for b in thread.get("beats", []) if b.get("id") == artifact.get("beat")),
        None,
    )
    thread_context = {
        "title": thread.get("title"),
        "window": thread.get("window"),
        "driver": thread.get("driver"),
        "cast": thread.get("cast"),
        "linked_products": thread.get("linked_products"),
        "linked_repos": thread.get("linked_repos"),
        "tensions": thread.get("tensions"),
        "relevant_beat": relevant_beat,
        "this_artifact": artifact,
    }
    user_prompt = (
        "Thread context:\n"
        + yaml.safe_dump(thread_context, sort_keys=False, width=120)
        + f"\nWrite the {kind} document now. "
        "Output ONLY the markdown body — no preamble, no fences."
    )
    return chat(
        [{"role": "system", "content": sys_prompt},
         {"role": "user", "content": user_prompt}],
        budget=budget, cache_dir=CACHE, model=model,
        temperature=0.7, dry_run=dry_run, max_tokens=2500,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--budget-usd", type=float, default=5.0)
    ap.add_argument("--only", default=None,
                    help="only generate artifacts whose thread id matches")
    ap.add_argument("--model", default="deepseek-chat")
    args = ap.parse_args()

    facts = yaml.safe_load(FACTS.read_text())
    thread_files = sorted(THREADS.glob("THR-*.yaml"))
    if args.only:
        thread_files = [t for t in thread_files if args.only in t.name]
    if not thread_files:
        raise SystemExit("no threads found; run 03_plan_threads.py first")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    budget = Budget(cap_usd=args.budget_usd)
    written = 0
    skipped = 0

    for tfp in thread_files:
        thread = yaml.safe_load(tfp.read_text())
        for art in thread.get("artifacts", []) or []:
            out_name = f"{thread['id']}__{art.get('beat','B?')}__{_slug(art.get('title',''))}.md"
            out_path = ARTIFACTS / out_name
            if out_path.exists() and not args.only:
                skipped += 1
                continue
            print(f"[{thread['id']}] {art.get('kind')} → {out_name}", file=sys.stderr)
            body = _generate_one(facts, thread, art, budget,
                                 dry_run=args.dry_run, model=args.model)
            if body is None:
                continue
            if args.dry_run:
                continue
            out_path.write_text(body.strip() + "\n")
            written += 1
            print(f"  wrote {out_path.name}  budget={budget.summary()}", file=sys.stderr)

    print(f"\nDONE  written={written}  skipped={skipped}  {budget.summary()}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
