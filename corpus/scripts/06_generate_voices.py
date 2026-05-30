#!/usr/bin/env python3
"""06_generate_voices.py — one LLM call per teammate → voice profile + snippet pool.

Output is a single ``voices.yaml`` file in ``facts/`` keyed by ``person:<handle>``,
read by ``05_render_events.py`` instead of the global BEAT_SLACK template bank.

Why: templated messages all sound the same; Fyralis's model layer can't surface
behavioral patterns ("Prajwal is terse, deflects on bad news, Mondays-only") from
prose that's interchangeable across people. Per-person snippet pools give every
IC a recognizable voice that the renderer just picks from.

Cost: 34 calls × Codex CLI overhead. ~5–10 min on a warm subscription; $0 on
the API path. Cached on disk by person handle so re-runs are free.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _llm import Budget, chat            # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FACTS = ROOT / "facts" / "facts.yaml"
OUT = ROOT / "facts" / "voices.yaml"
CACHE = ROOT / "cache" / "voices"


SYSTEM = """You are profiling a single teammate at a Bitcoin/ZK protocol startup \
(Alpen Labs) and producing a "voice profile" + a bank of short Slack message \
snippets that match how this specific person talks.

You will get the person's real handle, role, team, tenure, top repos, and any \
public bio you have on them. Build a coherent character for them. Then write \
the snippets so their messages sound like that character — not like a generic \
engineer.

REQUIREMENTS for the snippets:
- Real Slack tone. Lowercase by default, abbreviations, technical shorthand.
- NO ticket numbers / PR numbers / commit hashes. Real engineers say
  "shipped that thing" or "merged the bridge fix", not "merged #1834".
- NO marketing language, no exclamation points unless the character would use them.
- 2-25 words per snippet. A mix of short and slightly longer.
- The same person sounds the same across all categories — same vocab, same vibe.

OUTPUT FORMAT (YAML, no fences, no commentary):

person: person:<handle>
voice:
  style: <one of: terse, verbose, deadpan, warm, formal, casual, technical, chaotic>
  characteristics:
    - <2-5 short phrases capturing how they communicate>
  typical_concerns:
    - <2-4 things they fixate on — e.g. "test coverage", "audit findings", "perf">
  reaction_to_bad_news: <one of: owns_and_debugs, deflects, makes_a_joke, goes_quiet, escalates>
  active_hours: <one of: mornings, evenings, late_night, weekends_heavy, normal>
snippets:
  kickoff:    [ "..." , "..." , "..." , "..." , "..." ]   # 5 snippets
  design:     [ "...", ... ]                              # 5
  impl:       [ "...", ... ]                              # 5
  review:     [ "...", ... ]                              # 5
  audit:      [ "...", ... ]                              # 5
  ship:       [ "...", ... ]                              # 5
  postmortem: [ "...", ... ]                              # 5
  retro:      [ "...", ... ]                              # 5
  hiring:     [ "...", ... ]                              # 5
"""


def _person_blob(p: dict) -> str:
    return yaml.safe_dump({
        "handle": p["github_handle"],
        "name": p["full_name"] if p["full_name"] != "needs:review" else p["github_handle"],
        "role": p["role"],
        "level": p["level"],
        "team": p["team"],
        "joined": p["started_at"],
        "commits": p["commits"],
        "top_repos": p.get("top_repos", [])[:5],
    }, sort_keys=False, width=120, allow_unicode=True)


def _one(person: dict, budget: Budget, dry_run: bool, model: str) -> dict | None:
    user = f"Profile this teammate:\n\n{_person_blob(person)}\n\nGenerate the voice profile + snippets now."
    raw = chat([{"role": "system", "content": SYSTEM},
                {"role": "user", "content": user}],
               budget=budget, cache_dir=CACHE, model=model,
               temperature=0.85, dry_run=dry_run, max_tokens=2500)
    if dry_run:
        return None
    try:
        spec = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        print(f"  [warn] {person['github_handle']}: YAML parse failed: {e}", file=sys.stderr)
        return None
    if not isinstance(spec, dict) or "voice" not in spec or "snippets" not in spec:
        print(f"  [warn] {person['github_handle']}: spec malformed", file=sys.stderr)
        return None
    return spec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--budget-usd", type=float, default=5.0)
    ap.add_argument("--only", default=None,
                    help="only generate for handles matching this substring")
    ap.add_argument("--model", default="deepseek-chat")
    args = ap.parse_args()

    facts = yaml.safe_load(FACTS.read_text())
    people = facts["people"]
    if args.only:
        people = [p for p in people if args.only in p["github_handle"]]
    print(f"voices to plan: {len(people)}", file=sys.stderr)

    budget = Budget(cap_usd=args.budget_usd)
    voices: dict[str, dict] = {}
    if OUT.exists() and not args.only:
        existing = yaml.safe_load(OUT.read_text()) or {}
        voices = existing.get("voices") or {}

    for i, p in enumerate(people, start=1):
        pid = p["id"]
        if pid in voices and not args.only:
            print(f"[{i}/{len(people)}] {p['github_handle']} (cached)", file=sys.stderr)
            continue
        print(f"[{i}/{len(people)}] {p['github_handle']} ({p['role']}/{p['team']})",
              file=sys.stderr)
        spec = _one(p, budget, dry_run=args.dry_run, model=args.model)
        if spec is None:
            continue
        voices[pid] = spec
        # Incremental write so a crashed run doesn't lose progress.
        OUT.write_text(yaml.safe_dump({"voices": voices}, sort_keys=False,
                                      width=120, allow_unicode=True))
        print(f"  wrote voices/{pid}  budget={budget.summary()}", file=sys.stderr)

    print(f"\nDONE  voices={len(voices)}  {budget.summary()}", file=sys.stderr)


if __name__ == "__main__":
    main()
