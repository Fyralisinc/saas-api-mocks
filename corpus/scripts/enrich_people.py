#!/usr/bin/env python3
"""Enrich people in facts.yaml from public GitHub profile data + heuristics.

For each contributor:
  1. GET /users/{login} → name, bio, company, location, blog, twitter, email
  2. Infer team from their top_repos (deterministic mapping)
  3. Infer level from total commits + tenure
  4. Infer role keyword from bio (founder/research/devrel/...)

Whatever the GitHub profile gave us is authoritative; gaps stay
'needs:review' so the user can spot-check rather than data-entry from scratch.
Writes facts/people.enriched.yaml — the user merges into facts.yaml.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
DRAFT = ROOT / "facts" / "facts.yaml.draft"
FACTS_OUT = ROOT / "facts" / "facts.yaml"
DETAIL_OUT = ROOT / "facts" / "people.enriched.yaml"
CACHE = ROOT / "cache" / "gh_users"
TOKEN = os.environ.get("GITHUB_TOKEN", "")

# Repo → team mapping. Edit as you learn more.
REPO_TEAM = {
    # Protocol core
    "alpen": "team:protocol",
    "strata-common": "team:protocol",
    "moho": "team:protocol",
    "asm": "team:protocol",
    "ssz-gen": "team:protocol",
    "strata-p2p": "team:protocol",
    # Bridge
    "strata-bridge": "team:bridge",
    "bridge-sm-design-docs": "team:bridge",
    # Research / crypto
    "mosaic": "team:research",
    "mosaic-torrent": "team:research",
    "BitVM": "team:research",
    "zkaleido": "team:research",
    "g16": "team:research",
    "ckt": "team:research",
    "dv-pari": "team:research",
    "dv-pari-circuit": "team:research",
    "garbled-circuits": "team:research",
    "verifiable-garbling": "team:research",
    "garbled-snark-verifier": "team:research",
    "duty-free-bits": "team:research",
    "fastmul": "team:research",
    # Bitcoin libs / infra
    "bitcoind-async-client": "team:infra",
    "bitcoin-bosd": "team:infra",
    "bitcoin_signet": "team:infra",
    "rust-template": "team:infra",
    "rust-template-workspace": "team:infra",
    "make_buf": "team:infra",
    "blockscout-rs": "team:infra",
    "checkpoint-explorer": "team:infra",
    "alpen-faucet": "team:infra",
    "faucet-api": "team:infra",
    "risc0.nix": "team:infra",
    "sp1.nix": "team:infra",
    # Dashboards / ops
    "alpen-dashboards": "team:ops",
    ".github": "team:ops",
    "docs-archive": "team:devrel",
}


def gh_user(client: httpx.Client, login: str) -> dict | None:
    """Fetch /users/{login}, cached on disk."""
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / f"{login}.json"
    if cached.exists():
        return json.loads(cached.read_text())
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "alpen-corpus"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    for attempt in range(3):
        r = client.get(f"https://api.github.com/users/{login}",
                       headers=headers, timeout=30)
        if r.status_code == 200:
            cached.write_text(r.text)
            return r.json()
        if r.status_code in (403, 429):
            reset = int(r.headers.get("X-RateLimit-Reset", "0"))
            sleep_for = max(15, reset - int(time.time())) + 5
            time.sleep(sleep_for)
            continue
        if r.status_code == 404:
            return None
        time.sleep(2 ** attempt)
    return None


def infer_team(top_repos: list[str]) -> str:
    """Pick the team that owns the plurality of this person's top repos."""
    from collections import Counter
    votes = Counter()
    for r in top_repos:
        team = REPO_TEAM.get(r)
        if team:
            votes[team] += 1
    if not votes:
        return "needs:review"
    return votes.most_common(1)[0][0]


# Strong role-keyword markers in GitHub bios. Highest-priority first.
ROLE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("founder",   re.compile(r"\b(founder|co-?founder|ceo|cto)\b", re.I)),
    ("research",  re.compile(r"\b(research|cryptograph|crypto|phd|prof|professor|mathematic|logic|complexity|computer\s+science)\b", re.I)),
    ("devrel",    re.compile(r"\b(devrel|developer\s+relations|advocate|community)\b", re.I)),
    ("security",  re.compile(r"\b(security|audit)\b", re.I)),
    ("infra",     re.compile(r"\b(infra|infrastructure|devops|sre|systems)\b", re.I)),
    ("engineer",  re.compile(r"\b(engineer|developer|programmer|softw|building|code|cypherpunk)\b", re.I)),
]


def infer_role(bio: str | None, team: str) -> str:
    """Bio keywords first; fall back to team-implied default ('engineer' for
    most teams; 'researcher' for team:research; 'devrel' for team:devrel)."""
    if bio:
        for label, pat in ROLE_PATTERNS:
            if pat.search(bio):
                return label
    return {
        "team:research": "research",
        "team:devrel":   "devrel",
        "team:ops":      "ops",
    }.get(team, "engineer")


def infer_level(commits: int, started_at: str, last_active: str) -> str:
    """Crude: long-tenured + high-commit ⇒ senior; short tenure ⇒ ic."""
    from datetime import datetime
    try:
        tenure_days = (datetime.fromisoformat(last_active) -
                       datetime.fromisoformat(started_at)).days
    except Exception:
        tenure_days = 0
    if commits > 400 and tenure_days > 365:
        return "senior"
    if commits > 100:
        return "ic"
    return "ic"


def looks_external(profile: dict | None, commits: int) -> bool:
    """Heuristic for non-Alpen contributors.

    True when their GitHub 'company' field names a different organization.
    Also true for very low-commit no-profile contributors (likely drive-by).
    """
    if not profile:
        return commits < 5
    company = (profile.get("company") or "").lower().strip().lstrip("@")
    if company and "alpen" not in company:
        return True
    return False


def main() -> None:
    source = FACTS_OUT if FACTS_OUT.exists() else DRAFT
    facts = yaml.safe_load(source.read_text())
    enriched = []
    with httpx.Client(follow_redirects=True) as client:
        for person in facts["people"]:
            login = person["github_handle"]
            profile = gh_user(client, login)
            if profile is None:
                profile = {}

            new = dict(person)
            # Keep user-set values; only auto-fill fields that are still
            # 'needs:review' or empty. Lets you re-run after hand-edits without
            # losing them.
            def _keep(field: str, candidate: str) -> str:
                current = person.get(field) or ""
                if current and current != "needs:review":
                    return current
                return candidate

            new["full_name"] = _keep("full_name", profile.get("name") or "needs:review")
            # Bio/company/location/socials always reflect the latest GitHub
            # profile — they're metadata, not editorial choices.
            new["bio"] = profile.get("bio") or ""
            new["company_self_reported"] = profile.get("company") or ""
            new["location"] = profile.get("location") or ""
            new["twitter"] = profile.get("twitter_username") or ""
            new["blog_url"] = profile.get("blog") or ""
            if profile.get("email") and (person.get("email") or "").endswith("@alpenlabs.io"):
                # Override only if user hasn't already set a custom email.
                new["email"] = profile["email"]

            new["team"]  = _keep("team",  infer_team(person["top_repos"]))
            new["role"]  = _keep("role",  infer_role(profile.get("bio"), new["team"]))
            new["level"] = _keep("level", infer_level(
                person["commits"], person["started_at"], person["last_active"],
            ))
            new["likely_external"] = looks_external(profile, person["commits"])
            enriched.append(new)

    # Detail file (kept for diffing).
    with DETAIL_OUT.open("w") as f:
        yaml.safe_dump({"people": enriched}, f, sort_keys=False,
                       width=120, allow_unicode=True)

    # Trim to the canonical fields the L2/L3 pipeline needs.
    trimmed = []
    for p in enriched:
        if p.get("likely_external"):
            continue           # drop non-Alpen contributors
        trimmed.append({
            "id": p["id"],
            "github_handle": p["github_handle"],
            "full_name": p["full_name"],
            "email": p["email"],
            "role": p["role"],
            "level": p["level"],
            "team": p["team"],
            "started_at": p["started_at"],
            "last_active": p["last_active"],
            "twitter": p.get("twitter") or None,
            "blog_url": p.get("blog_url") or None,
            "bio": p.get("bio") or "",
            "commits": p["commits"],
            "top_repos": p["top_repos"],
        })
    facts["people"] = trimmed
    with FACTS_OUT.open("w") as f:
        yaml.safe_dump(facts, f, sort_keys=False, width=120, allow_unicode=True)
    print(f"wrote {FACTS_OUT}  (people={len(trimmed)} after dropping externals)")

    # Summary
    by_team = {}
    by_role = {}
    needs_review = 0
    external = 0
    for p in enriched:
        by_team[p["team"]] = by_team.get(p["team"], 0) + 1
        by_role[p["role"]] = by_role.get(p["role"], 0) + 1
        if p["full_name"] == "needs:review" or p["role"] == "needs:review" or p["team"] == "needs:review":
            needs_review += 1
        if p["likely_external"]:
            external += 1

    print(f"wrote {DETAIL_OUT}")
    print(f"  enriched={len(enriched)}  needs:review={needs_review}  likely_external={external}")
    print("  by team:", by_team)
    print("  by role:", by_role)


if __name__ == "__main__":
    main()
