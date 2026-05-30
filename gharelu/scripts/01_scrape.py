#!/usr/bin/env python3
"""01_scrape.py — pull real public data for Alpen Labs into raw/.

Three sources:
  1. GitHub API → orgs/alpenlabs/* metadata, commits, PRs, issues, releases,
     contributors. Saved to raw/github/<repo>/{repo.json,commits.jsonl,
     pulls.jsonl,issues.jsonl,releases.jsonl,contributors.json}.
  2. alpenlabs.io blog → raw/blog/<slug>.html
  3. docs.alpenlabs.io → raw/docs/ (best-effort recursive)

Requires GITHUB_TOKEN in env (rate limit: 5000/hr auth vs 60 unauth).
Idempotent: re-running re-fetches everything. For incremental, blow away
the targeted subtree first.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Iterator
from urllib.parse import urljoin, urlparse

import httpx

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
GH_BASE = "https://api.github.com"
ORG = "alpenlabs"

TOKEN = os.environ.get("GITHUB_TOKEN", "")
HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "alpen-corpus-scraper",
}
if TOKEN:
    HEADERS["Authorization"] = f"Bearer {TOKEN}"


def gh_get(client: httpx.Client, path: str, params: dict | None = None) -> httpx.Response:
    """Authenticated GET with primary-rate-limit backoff."""
    url = path if path.startswith("http") else f"{GH_BASE}{path}"
    for attempt in range(5):
        r = client.get(url, params=params, headers=HEADERS, timeout=60.0)
        if r.status_code == 200:
            remaining = int(r.headers.get("X-RateLimit-Remaining", "1000"))
            if remaining < 50:
                reset = int(r.headers.get("X-RateLimit-Reset", "0"))
                sleep_for = max(0, reset - int(time.time())) + 5
                print(f"  [rate-limit] {remaining} left; sleeping {sleep_for}s", flush=True)
                time.sleep(sleep_for)
            return r
        if r.status_code in (403, 429):
            reset = int(r.headers.get("X-RateLimit-Reset", "0"))
            sleep_for = max(30, reset - int(time.time())) + 5
            print(f"  [throttle] {r.status_code}; sleeping {sleep_for}s (attempt {attempt+1})", flush=True)
            time.sleep(sleep_for)
            continue
        if r.status_code == 404:
            return r
        print(f"  [err] {r.status_code} on {url}: {r.text[:200]}", flush=True)
        time.sleep(2 ** attempt)
    return r


def paginate(client: httpx.Client, path: str, params: dict | None = None, max_pages: int = 1000) -> Iterator[dict]:
    """Walk Link-header pagination yielding individual records."""
    params = dict(params or {})
    params.setdefault("per_page", 100)
    url = f"{GH_BASE}{path}"
    pages = 0
    while url and pages < max_pages:
        r = gh_get(client, url, params=params if pages == 0 else None)
        if r.status_code != 200:
            return
        for item in r.json():
            yield item
        link = r.headers.get("Link", "")
        next_url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                next_url = part.split(";")[0].strip().strip("<>")
                break
        url = next_url
        pages += 1


def write_jsonl(path: Path, items: Iterator[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w") as f:
        for item in items:
            f.write(json.dumps(item) + "\n")
            n += 1
    return n


def scrape_repo(client: httpx.Client, name: str) -> dict:
    """Scrape one repo. Returns a summary dict."""
    base = RAW / "github" / name
    base.mkdir(parents=True, exist_ok=True)

    repo_r = gh_get(client, f"/repos/{ORG}/{name}")
    if repo_r.status_code != 200:
        return {"name": name, "error": repo_r.status_code}
    repo = repo_r.json()
    (base / "repo.json").write_text(json.dumps(repo, indent=2))

    # Commits — paginated, all branches via default branch only (cheap).
    n_commits = write_jsonl(
        base / "commits.jsonl",
        paginate(client, f"/repos/{ORG}/{name}/commits", max_pages=500),
    )

    # PRs — state=all to get open + closed + merged.
    n_pulls = write_jsonl(
        base / "pulls.jsonl",
        paginate(client, f"/repos/{ORG}/{name}/pulls", params={"state": "all"}, max_pages=200),
    )

    # Issues — includes PRs in GitHub's data model, but we keep both.
    n_issues = write_jsonl(
        base / "issues.jsonl",
        paginate(client, f"/repos/{ORG}/{name}/issues", params={"state": "all"}, max_pages=200),
    )

    # Releases.
    n_releases = write_jsonl(
        base / "releases.jsonl",
        paginate(client, f"/repos/{ORG}/{name}/releases", max_pages=20),
    )

    # Contributors (single page suffices).
    contrib_r = gh_get(client, f"/repos/{ORG}/{name}/contributors", params={"per_page": 100, "anon": "false"})
    if contrib_r.status_code == 200:
        (base / "contributors.json").write_text(json.dumps(contrib_r.json(), indent=2))
        n_contrib = len(contrib_r.json())
    else:
        n_contrib = 0

    return {
        "name": name,
        "commits": n_commits,
        "pulls": n_pulls,
        "issues": n_issues,
        "releases": n_releases,
        "contributors": n_contrib,
    }


def scrape_github(client: httpx.Client) -> None:
    """Scrape org + every repo."""
    print(f"=== GitHub: {ORG} ===", flush=True)
    org_r = gh_get(client, f"/orgs/{ORG}")
    (RAW / "github" / "org.json").write_text(json.dumps(org_r.json(), indent=2))

    members_r = gh_get(client, f"/orgs/{ORG}/public_members", params={"per_page": 100})
    if members_r.status_code == 200:
        (RAW / "github" / "public_members.json").write_text(json.dumps(members_r.json(), indent=2))

    repos = json.loads((RAW / "github" / "repos.json").read_text())
    summaries = []
    for i, repo in enumerate(repos, 1):
        name = repo["name"]
        print(f"[{i}/{len(repos)}] {name} (stars={repo['stargazers_count']}, archived={repo['archived']})", flush=True)
        s = scrape_repo(client, name)
        summaries.append(s)
        print(f"  -> commits={s.get('commits')} pulls={s.get('pulls')} issues={s.get('issues')} releases={s.get('releases')} contrib={s.get('contributors')}", flush=True)

    (RAW / "github" / "summary.json").write_text(json.dumps(summaries, indent=2))
    totals = {k: sum(s.get(k, 0) for s in summaries) for k in ("commits", "pulls", "issues", "releases", "contributors")}
    print(f"=== GitHub TOTALS: {totals} ===", flush=True)


# Blog posts known from manual discovery. Order matches site reverse-chronological.
BLOG_POSTS = [
    "btc-credit-markets",
    "introducing-mosaic-glocks-final-piece",
    "duty-free-bits-bitcoin",
    "inside-alpens-2025",
    "prague-testnet",
    "starknet-shared-glock-verifier",
    "glock-is-here",
    "alpen-testnet",
    "glock-verification-on-bitcoin",
    "bitcoin-dollar",
    "strategic-round",
    "introducing-the-strata-bridge",
    "state-of-snark-verification-with-bitvm2",
    "releasing-tmul",
    "proof-of-work",
    "current-state-of-snarks",
    "the-rise-of-snarks",
    "snarknado-practical-round-efficient-snark-verifier-on-bitcoin",
    "alpen-at-the-convergence-of-two-revolutions",
]


def scrape_blog(client: httpx.Client) -> None:
    print("=== Blog: alpenlabs.io ===", flush=True)
    out = RAW / "blog"
    out.mkdir(parents=True, exist_ok=True)
    for slug in BLOG_POSTS:
        url = f"https://www.alpenlabs.io/blog/{slug}"
        r = client.get(url, timeout=60.0)
        if r.status_code != 200:
            print(f"  [skip] {slug}: HTTP {r.status_code}", flush=True)
            continue
        (out / f"{slug}.html").write_text(r.text)
        print(f"  [ok] {slug} ({len(r.text)} bytes)", flush=True)
    (out / "_index.json").write_text(json.dumps([{"slug": s, "url": f"https://www.alpenlabs.io/blog/{s}"} for s in BLOG_POSTS], indent=2))


def scrape_docs(client: httpx.Client) -> None:
    """Best-effort: pull the docs landing and any directly-linked pages."""
    print("=== Docs: docs.alpenlabs.io ===", flush=True)
    out = RAW / "docs"
    out.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    queue = ["https://docs.alpenlabs.io/"]
    while queue and len(seen) < 100:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            r = client.get(url, timeout=30.0, follow_redirects=True)
        except Exception as e:
            print(f"  [err] {url}: {e}", flush=True)
            continue
        if r.status_code != 200:
            print(f"  [skip] {url}: HTTP {r.status_code}", flush=True)
            continue
        path = urlparse(url).path or "/"
        safe = path.strip("/").replace("/", "_") or "_index"
        (out / f"{safe}.html").write_text(r.text)
        print(f"  [ok] {url} ({len(r.text)} bytes)", flush=True)
        # Naive link discovery — only follow same-host hrefs.
        import re
        for href in re.findall(r'href="([^"]+)"', r.text):
            if href.startswith("#"):
                continue
            link = urljoin(url, href)
            if "docs.alpenlabs.io" in link and link.split("#")[0] not in seen:
                queue.append(link.split("#")[0])


def main() -> None:
    if not TOKEN:
        print("WARNING: GITHUB_TOKEN not set; rate limit is 60/hr (unauthenticated)", flush=True)
    with httpx.Client(follow_redirects=True) as client:
        what = sys.argv[1] if len(sys.argv) > 1 else "all"
        if what in ("all", "github"):
            scrape_github(client)
        if what in ("all", "blog"):
            scrape_blog(client)
        if what in ("all", "docs"):
            scrape_docs(client)
    print("=== DONE ===", flush=True)


if __name__ == "__main__":
    main()
