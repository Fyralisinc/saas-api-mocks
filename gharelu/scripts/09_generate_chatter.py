#!/usr/bin/env python3
"""09_generate_chatter.py — per-person off-topic snippet bank.

The voices.yaml bank covers WORK content (kickoff, design, impl, review, …).
This script generates the NON-work side: reactions, banter, news takes, PTO
announces, conference posts. Same per-person voice — same individual sounds
the same on a #random chat as on a #strata-launch review.

Output: facts/chatter.yaml keyed by person:<handle>.

Cost: 36 calls × Codex CLI overhead. ~$0 on subscription, ~15 min wall clock.
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
VOICES = ROOT / "facts" / "voices.yaml"
OUT = ROOT / "facts" / "chatter.yaml"
CACHE = ROOT / "cache" / "chatter"


SYSTEM = """You are generating the NON-WORK side of one teammate's Slack \
presence at a Bitcoin/ZK protocol startup (Alpen Labs). You already have \
their voice profile for work content (technical reviews, design discussions). \
Now write their off-topic snippets — the messages they'd send in #random, \
#memes, or as banter in work channels.

CRITICAL: they must sound IDENTICAL to their work voice. Same vocabulary, \
same cadence, same energy. A terse engineer doesn't suddenly write emoji-laden \
paragraphs in #random. A formal voice doesn't suddenly become casual.

REQUIREMENTS:
- Real Slack tone. Lowercase by default. Abbreviations welcome.
- NO ticket numbers or PR numbers.
- NO marketing language.
- 2-25 words per snippet. Mix shorter and longer.
- Pretty much all messages should feel like normal human Slack, not like \
  scripted/generic banter. Real engineers post things like "cat sat on the keyboard, \
  this PR is now feline-reviewed" or "btcd memleak is back" or just "lol".

OUTPUT FORMAT (YAML, no fences, no commentary):

person: person:<handle>
chatter:
  reactions:                 # 8 ULTRA-SHORT replies (1-4 words / emoji)
    - "..."
  random:                    # 10 standalone off-topic msgs (#random etc.)
    - "..."
  pto_announce:              # 4 "i'll be out" announces, in their voice
    - "..."
  return_from_pto:           # 4 "back, catching up" messages
    - "..."
  conference_post:           # 4 "at conference X" or "anyone else at btc++" msgs
    - "..."
  incident_reaction:         # 4 reactions to a security disclosure / outage / urgent thing
    - "..."
  news_take:                 # 4 takes on Bitcoin/ZK news (a paper, a release, vitalik post, etc.)
    - "..."
  weekend_chatter:           # 4 weekend-flavored msgs ("anyone tried X this weekend", etc.)
    - "..."
"""


def _voice_blob(person_id: str, voices: dict) -> str:
    v = voices.get(person_id, {})
    return yaml.safe_dump({
        "handle": v.get("person", person_id),
        "voice": v.get("voice", {}),
        "sample_work_snippets": (v.get("snippets") or {}).get("impl", [])[:3],
    }, sort_keys=False, width=120, allow_unicode=True)


def _one(person: dict, voices: dict, budget: Budget, dry_run: bool, model: str) -> dict | None:
    user = (f"Generate the off-topic chatter for this teammate. Their already-known voice:\n\n"
            f"{_voice_blob(person['id'], voices)}\n"
            "Write the chatter now. Match their voice exactly.")
    raw = chat([{"role": "system", "content": SYSTEM},
                {"role": "user", "content": user}],
               budget=budget, cache_dir=CACHE, model=model,
               temperature=0.85, dry_run=dry_run, max_tokens=2200)
    if dry_run:
        return None
    try:
        spec = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        print(f"  [warn] {person['github_handle']}: YAML parse failed: {e}", file=sys.stderr)
        return None
    if not isinstance(spec, dict) or "chatter" not in spec:
        print(f"  [warn] {person['github_handle']}: spec malformed", file=sys.stderr)
        return None
    return spec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--budget-usd", type=float, default=5.0)
    ap.add_argument("--only", default=None)
    ap.add_argument("--model", default="deepseek-chat")
    args = ap.parse_args()

    facts = yaml.safe_load(FACTS.read_text())
    voices = (yaml.safe_load(VOICES.read_text()) or {}).get("voices") or {}
    if not voices:
        raise SystemExit("voices.yaml is empty; run 06_generate_voices first")

    people = facts["people"]
    if args.only:
        people = [p for p in people if args.only in p["github_handle"]]
    print(f"chatter to generate: {len(people)}", file=sys.stderr)

    budget = Budget(cap_usd=args.budget_usd)
    chatter: dict[str, dict] = {}
    if OUT.exists() and not args.only:
        existing = yaml.safe_load(OUT.read_text()) or {}
        chatter = existing.get("chatter_by_person") or {}

    for i, p in enumerate(people, start=1):
        pid = p["id"]
        if pid in chatter and not args.only:
            print(f"[{i}/{len(people)}] {p['github_handle']} (cached)", file=sys.stderr)
            continue
        print(f"[{i}/{len(people)}] {p['github_handle']}", file=sys.stderr)
        spec = _one(p, voices, budget, dry_run=args.dry_run, model=args.model)
        if spec is None:
            continue
        chatter[pid] = spec.get("chatter") or {}
        OUT.write_text(yaml.safe_dump({"chatter_by_person": chatter},
                                       sort_keys=False, width=120,
                                       allow_unicode=True))
        print(f"  wrote chatter/{pid}  budget={budget.summary()}", file=sys.stderr)

    print(f"\nDONE  chatter={len(chatter)}  {budget.summary()}", file=sys.stderr)


if __name__ == "__main__":
    main()
