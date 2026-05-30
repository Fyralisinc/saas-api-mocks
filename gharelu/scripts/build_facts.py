#!/usr/bin/env python3
"""Compile a draft facts.yaml from the scraped real data.

This is the L1 layer: people, repos, products, milestones. Most fields come
straight from the scrape; a few (full_name, role, level, team) require
hand-entry from LinkedIn and are emitted as ``needs:linkedin``.

Re-running overwrites facts.yaml.draft so user edits to facts.yaml are safe.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw" / "github"
BLOG = ROOT / "raw" / "blog"
OUT = ROOT / "facts" / "facts.yaml.draft"

# Repos that are clearly upstream forks but aren't flagged as forks (e.g.
# vendored research) get filtered manually — extend as needed.
FORCE_EXCLUDE = set()

# Bots / non-human contributors to mark as such.
BOTS = {"bors", "github-actions[bot]", "dependabot[bot]", "renovate[bot]",
        "alpen-labs-bot", "alpen-bot"}


def load_repos() -> list[dict]:
    repos = json.loads((RAW / "repos.json").read_text())
    return [r for r in repos if not r["fork"] and r["name"] not in FORCE_EXCLUDE]


def first_last_per_contributor(own_repos: list[dict]) -> dict[str, dict]:
    """For each contributor: first/last commit date + which repos they touched."""
    by_user: dict[str, dict] = defaultdict(lambda: {
        "commits": 0, "first": None, "last": None, "repos": Counter(),
    })
    for r in own_repos:
        commits_file = RAW / r["name"] / "commits.jsonl"
        if not commits_file.exists():
            continue
        with commits_file.open() as f:
            for line in f:
                c = json.loads(line)
                author = c.get("author") or {}
                login = author.get("login")
                if not login or login in BOTS:
                    continue
                date = c["commit"]["author"]["date"]
                u = by_user[login]
                u["commits"] += 1
                u["repos"][r["name"]] += 1
                if u["first"] is None or date < u["first"]:
                    u["first"] = date
                if u["last"] is None or date > u["last"]:
                    u["last"] = date
    return by_user


def extract_blog_milestones() -> list[dict]:
    """Parse blog HTML for title + ISO date, return milestone records."""
    out = []
    index = json.loads((BLOG / "_index.json").read_text())
    for entry in index:
        slug = entry["slug"]
        html_path = BLOG / f"{slug}.html"
        if not html_path.exists():
            continue
        html = html_path.read_text()
        # Pull og:title and og:article:published_time if present (sitebuilders embed both).
        m_title = re.search(r'<meta property="og:title" content="([^"]+)"', html)
        m_date = re.search(r'<meta property="article:published_time" content="([^"]+)"', html)
        title = m_title.group(1).strip() if m_title else slug.replace("-", " ").title()
        date = m_date.group(1)[:10] if m_date else None
        out.append({"date": date, "slug": slug, "title": title,
                    "url": entry["url"]})
    # Sort chronologically
    out.sort(key=lambda x: x["date"] or "0")
    return out


def main() -> None:
    own = load_repos()
    own.sort(key=lambda r: (-r["stargazers_count"], r["name"]))

    repos_yaml = []
    for r in own:
        repos_yaml.append({
            "name": r["name"],
            "id": f"repo:{r['name']}",
            "description": r["description"] or "",
            "language": r["language"] or "",
            "stars": r["stargazers_count"],
            "created_at": r["created_at"][:10],
            "archived": r["archived"],
            "private": False,
        })

    by_user = first_last_per_contributor(own)
    people_yaml = []
    for login, u in sorted(by_user.items(), key=lambda x: -x[1]["commits"]):
        if u["commits"] < 3:
            continue            # drop one-off contributors (likely external)
        top_repos = [name for name, _ in u["repos"].most_common(5)]
        people_yaml.append({
            "id": f"person:{login}",
            "github_handle": login,
            "full_name": "needs:linkedin",
            "email": f"{login}@alpenlabs.io",            # best-effort guess
            "role": "needs:linkedin",
            "level": "needs:linkedin",
            "team": "needs:linkedin",
            "started_at": u["first"][:10] if u["first"] else None,
            "last_active": u["last"][:10] if u["last"] else None,
            "commits": u["commits"],
            "top_repos": top_repos,
        })

    milestones = extract_blog_milestones()

    facts = {
        "_meta": {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "generator": "scripts/build_facts.py",
            "note": "DRAFT — review and copy to facts.yaml before editing. "
                    "Fields marked 'needs:linkedin' require hand-entry.",
        },
        "company": {
            "name": "Alpen Labs",
            "id": "company:alpen-labs",
            "mission": "Bitcoin's Own Financial System — borrow, earn, "
                       "and spend in dollars directly with Bitcoin.",
            "founded": "2022-01-01",                          # needs:verification
            "homepage": "https://www.alpenlabs.io",
            "github_org": "alpenlabs",
            "blog": "https://www.alpenlabs.io/blog",
            "docs": "https://docs.alpenlabs.io",
            "headcount_estimate": len(people_yaml),
        },
        "products": [
            {"id": "product:strata", "name": "Strata",
             "description": "Bitcoin rollup enabling programmability without "
                            "wrapped tokens or custodians."},
            {"id": "product:glock", "name": "Glock",
             "description": "SNARK verification standard for Bitcoin "
                            "(BitVM2-based)."},
            {"id": "product:mosaic", "name": "Mosaic",
             "description": "Garbled-circuit protocol for Bitcoin "
                            "verification (Glock's final piece)."},
            {"id": "product:strata-bridge", "name": "Strata Bridge",
             "description": "Reference implementation of the Strata bridge."},
            {"id": "product:btc-credit-markets", "name": "Bitcoin Credit Markets",
             "description": "Borrow against Bitcoin holdings."},
            {"id": "product:bitcoin-dollar", "name": "Bitcoin Dollar",
             "description": "Spend Bitcoin collateral in dollar-denominated form."},
        ],
        "milestones_from_blog": milestones,
        "milestones_curated": [
            # Hand-curated milestones from blog + public sources.
            {"date": "2024-04-09", "kind": "company.launch",
             "title": "Public launch / first blog post",
             "source": "blog/alpen-at-the-convergence-of-two-revolutions"},
            {"date": "2025-01-09", "kind": "fundraise",
             "title": "Strategic funding round",
             "source": "blog/strategic-round"},
            {"date": "2025-08-04", "kind": "product.launch",
             "title": "Public testnet live",
             "source": "blog/alpen-testnet"},
            {"date": "2025-08-19", "kind": "product.launch",
             "title": "Glock public release",
             "source": "blog/glock-is-here"},
            {"date": "2025-10-15", "kind": "partnership",
             "title": "Starknet shared Glock verifier collaboration",
             "source": "blog/starknet-shared-glock-verifier"},
            {"date": "2025-12-04", "kind": "product.update",
             "title": "Prague testnet support",
             "source": "blog/prague-testnet"},
            {"date": "2026-05-07", "kind": "product.launch",
             "title": "Mosaic announcement",
             "source": "blog/introducing-mosaic-glocks-final-piece"},
        ],
        "repos": repos_yaml,
        "people": people_yaml,
        "teams_template": [
            # Hand-edit. These IDs feed person.team in events.jsonl.
            {"id": "team:protocol", "name": "Protocol Engineering"},
            {"id": "team:research", "name": "Research"},
            {"id": "team:bridge", "name": "Bridge"},
            {"id": "team:infra", "name": "Infrastructure"},
            {"id": "team:devrel", "name": "DevRel"},
            {"id": "team:ops", "name": "Operations"},
        ],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as f:
        yaml.safe_dump(facts, f, sort_keys=False, width=120, allow_unicode=True)
    print(f"wrote {OUT}")
    print(f"  repos={len(repos_yaml)} people={len(people_yaml)} "
          f"blog_milestones={len(milestones)} curated_milestones={len(facts['milestones_curated'])}")


if __name__ == "__main__":
    main()
