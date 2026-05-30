#!/usr/bin/env python3
"""05_render_events.py — facts + threads + artifacts + GitHub mirror → events.jsonl.

This is the L4 stage. Pure Python templates (no LLM). Emits one JSON object
per event line, sorted by timestamp. Spammer's corpus.replay reads this.

Layout of the output stream:
  1) Bootstrap (t=facts.company.founded):
       org.team.create, org.person.create, github.user.create,
       github.repo.create, jira.project.create, notion.workspace.init,
       calendar.account, slack.workspace.init, drive.installation, …
  2) Real GitHub mirror from raw/github/*:
       github.commit, github.pr.open/close/merge, github.review.submit,
       github.issue.open/close/comment, github.release.publish
  3) Per-thread renderings:
       For each beat we drop a templated burst of slack.message + jira.* +
       (where the beat is design/audit/postmortem/retro) one notion.page.create
       linking to the LLM-generated artifact file.
  4) Recurring rituals:
       Weekly standups (calendar.event.create + recurring slack mentions),
       monthly all-hands, biweekly retros.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import yaml

ROOT = Path(__file__).resolve().parent.parent
FACTS = ROOT / "facts" / "facts.yaml"
VOICES = ROOT / "facts" / "voices.yaml"
PATTERNS = ROOT / "facts" / "patterns.yaml"
OFFICE_LIFE = ROOT / "facts" / "office_life.yaml"
CHATTER = ROOT / "facts" / "chatter.yaml"
THREADS = ROOT / "threads"
ARTIFACTS = ROOT / "artifacts"
GH_RAW = ROOT / "raw" / "github"
OUT = ROOT / "build" / "events.jsonl"


# Deterministic per-(person,repo) interaction sequence — keeps generated PR/
# issue numbers stable across re-runs without us tracking explicit state.
def _hash_int(*parts, n: int = 10000) -> int:
    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return int(h[:8], 16) % n


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _midday(day, hour: int = 10) -> datetime:
    """Yield a UTC mid-day datetime. ``day`` can be str (YYYY-MM-DD) or date."""
    if isinstance(day, datetime):
        d = day if day.tzinfo else day.replace(tzinfo=timezone.utc)
    elif isinstance(day, date):
        d = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    else:
        d = datetime.fromisoformat(str(day))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
    return d.replace(hour=hour, minute=0, second=0, microsecond=0)


# -----------------------------------------------------------------------------
# Bootstrap events
# -----------------------------------------------------------------------------

def bootstrap_events(facts: dict) -> Iterator[dict]:
    """Create org.* + provider workspace records at t=company.founded."""
    t0 = _iso(_midday(facts["company"]["founded"]))

    # Teams.
    for team in facts.get("teams_template", []):
        yield {"t": t0, "provider": "org", "kind": "team.create",
               "payload": {"id": team["id"], "name": team["name"]}}

    # People — emit sorted by started_at so seniority order in DB is sensible.
    people = sorted(facts["people"], key=lambda p: p.get("started_at") or "9999")
    for p in people:
        when = _iso(_midday(p.get("started_at") or facts["company"]["founded"]))
        yield {"t": when, "provider": "org", "kind": "person.create",
               "payload": {
                   "id": p["id"],
                   "handle": p["github_handle"],
                   "full_name": (p["full_name"] if p["full_name"] != "needs:review"
                                 else p["github_handle"]),
                   "email": f"{p['github_handle'].lower()}@alpenlabs.io",
                   "role": p["role"] if p["role"] != "needs:review" else "engineer",
                   "level": p["level"] if p["level"] != "needs:review" else "ic",
                   "team": p["team"] if p["team"] != "needs:review" else None,
                   "timezone": "UTC",
               }}
        # Also surface as a github user (mirrored handle).
        yield {"t": when, "provider": "github", "kind": "user.create",
               "payload": {"id": f"ghuser:{p['github_handle']}",
                           "login": p["github_handle"],
                           "name": p["full_name"] if p["full_name"] != "needs:review"
                                   else p["github_handle"]}}

    # Repos.
    for r in facts.get("repos", []):
        if r.get("archived"):
            continue
        when = _iso(_midday(r["created_at"][:10]))
        yield {"t": when, "provider": "github", "kind": "repo.create",
               "payload": {"id": r["id"], "name": r["name"],
                           "owner": "alpenlabs",
                           "default_branch": r.get("default_branch", "main"),
                           "description": r.get("description") or "",
                           "language": r.get("language") or ""}}


# -----------------------------------------------------------------------------
# Real GitHub mirror — ingest raw scrape into corpus events
# -----------------------------------------------------------------------------

def _ghuser_id(login: str | None) -> str | None:
    return f"ghuser:{login}" if login else None


def github_mirror_events(facts: dict) -> Iterator[dict]:
    """Replay each scraped commit/PR/issue/release as a corpus event."""
    known_handles = {p["github_handle"] for p in facts["people"]}
    for repo in facts.get("repos", []):
        if repo.get("archived"):
            continue
        name = repo["name"]
        base = GH_RAW / name
        if not base.exists():
            continue
        repo_id = repo["id"]

        # Commits.
        cf = base / "commits.jsonl"
        if cf.exists():
            for line in cf.open():
                c = json.loads(line)
                author = (c.get("author") or {}).get("login")
                t = (c.get("commit") or {}).get("author", {}).get("date") or c.get("commit", {}).get("committer", {}).get("date")
                if not t:
                    continue
                yield {"t": t, "provider": "github", "kind": "commit",
                       "actor": _ghuser_id(author),
                       "payload": {"repo": repo_id, "sha": c["sha"],
                                   "message": (c.get("commit", {}).get("message") or "")[:500],
                                   "in_org": author in known_handles}}

        # Pulls.
        pf = base / "pulls.jsonl"
        if pf.exists():
            for line in pf.open():
                pr = json.loads(line)
                user = (pr.get("user") or {}).get("login")
                opened = pr.get("created_at")
                if not opened:
                    continue
                yield {"t": opened, "provider": "github", "kind": "pr.open",
                       "actor": _ghuser_id(user),
                       "payload": {"repo": repo_id, "number": pr["number"],
                                   "title": (pr.get("title") or "")[:300],
                                   "head": (pr.get("head") or {}).get("ref"),
                                   "base": (pr.get("base") or {}).get("ref")}}

                # Synthetic reviews on a deterministic subset of PRs from team
                # members — voice-aware via voice.snippets.review, timing-aware
                # via review_response_hours per reviewer. Captures the review-
                # bottleneck and reviewer-style signals Fyralis should detect.
                author_pid = _person_id_for_login(facts, user)
                if author_pid:
                    yield from _synthetic_pr_reviews(
                        repo_id=repo_id, pr_number=pr["number"],
                        author_pid=author_pid, opened_at=opened,
                        closed_at=pr.get("merged_at") or pr.get("closed_at"),
                        repo_name=name, facts=facts,
                    )

                if pr.get("merged_at"):
                    yield {"t": pr["merged_at"], "provider": "github",
                           "kind": "pr.merge",
                           "actor": _ghuser_id(user),
                           "payload": {"repo": repo_id, "number": pr["number"],
                                       "merge_commit_sha": pr.get("merge_commit_sha")}}
                elif pr.get("closed_at"):
                    yield {"t": pr["closed_at"], "provider": "github",
                           "kind": "pr.close",
                           "actor": _ghuser_id(user),
                           "payload": {"repo": repo_id, "number": pr["number"],
                                       "merged": False}}

        # Issues. NB: GitHub's API lists PRs as issues too — skip those.
        ifn = base / "issues.jsonl"
        if ifn.exists():
            for line in ifn.open():
                it = json.loads(line)
                if "pull_request" in it:
                    continue
                user = (it.get("user") or {}).get("login")
                opened = it.get("created_at")
                if not opened:
                    continue
                yield {"t": opened, "provider": "github", "kind": "issue.open",
                       "actor": _ghuser_id(user),
                       "payload": {"repo": repo_id, "number": it["number"],
                                   "title": (it.get("title") or "")[:300],
                                   "labels": [l.get("name") for l in it.get("labels") or []]}}
                if it.get("closed_at"):
                    yield {"t": it["closed_at"], "provider": "github",
                           "kind": "issue.close",
                           "actor": _ghuser_id(user),
                           "payload": {"repo": repo_id, "number": it["number"]}}

        # Releases.
        rf = base / "releases.jsonl"
        if rf.exists():
            for line in rf.open():
                rel = json.loads(line)
                t = rel.get("published_at") or rel.get("created_at")
                if not t:
                    continue
                yield {"t": t, "provider": "github", "kind": "release.publish",
                       "payload": {"repo": repo_id, "tag": rel.get("tag_name"),
                                   "name": rel.get("name"),
                                   "draft": rel.get("draft", False),
                                   "prerelease": rel.get("prerelease", False)}}


# -----------------------------------------------------------------------------
# Per-thread renderings
# -----------------------------------------------------------------------------

# One Slack channel per thread, named from thread id.
def _slack_channel_for(thread_id: str) -> str:
    suffix = thread_id.split("-", 1)[-1] if thread_id.startswith("THR") else thread_id
    slug = re.sub(r"[^a-z0-9-]+", "-", suffix.lower()).strip("-")
    return f"channel:{slug[:60]}"


# Templated Slack message bank per beat kind. Picked deterministically by hash.
BEAT_SLACK = {
    "kickoff": [
        "kicking off — agenda: {summary}",
        "morning — first checkpoint at EOW",
        "spinning up the channel; doc coming today",
        "added everyone — flag if I missed you",
        "cool, will summarize after standup",
    ],
    "design": [
        "RFC up: {artifact_title} — would love eyes",
        "alt design considered: we'd add 1-2 days but cleaner",
        "the constraint we keep running into is {tension}",
        "+1 to that — let's go with the simpler option for now",
        "comment thread on the RFC — bumping it",
    ],
    "impl": [
        "PR opened — {summary}",
        "running into {tension} — pairing with {participant}?",
        "rebased on main, fixed conflicts",
        "blocked on review; ping when free",
        "tests green locally; pushing",
    ],
    "review": [
        "approved with nits",
        "left comments — mostly questions",
        "ok lgtm, please squash before merge",
        "found one edge case; flagged inline",
        "approving conditional on the typo fix",
    ],
    "audit": [
        "audit findings batch in tracker — {tension}",
        "responding to F-{idx}: not exploitable, here's why",
        "scope question for the auditors; following up",
        "fix landed — re-running their repro",
        "Halborn signed off on the diff",
    ],
    "ship": [
        "deploying now — eyes on metrics",
        "rolled to 50% — looking clean",
        "release notes draft up",
        "out — ship cake on me 🍰",
        "post-deploy graphs look good",
    ],
    "postmortem": [
        "RCA draft up: {artifact_title}",
        "follow-ups assigned in tracker",
        "action item: {tension}",
        "retro Friday 4pm",
        "owner check-in next week",
    ],
    "retro": [
        "wins / lessons going up",
        "voting on top 3 takeaways",
        "shout-out to {participant} for unblocking us",
        "added action items to tracker",
        "thanks all — see you next sprint",
    ],
    "hiring": [
        "two onsite loops scheduled",
        "offer extended to {participant}",
        "starting Monday — please welcome",
        "panel debrief at 4",
        "pipeline thin this week",
    ],
}


def _pick(opts: list[str], *seed_parts) -> str:
    if not opts:
        return ""
    return opts[_hash_int(*[str(p) for p in seed_parts], n=len(opts))]


def _load_voices() -> dict[str, dict]:
    """Per-person snippet pool — 06_generate_voices output. Empty dict if missing."""
    if not VOICES.exists():
        return {}
    data = yaml.safe_load(VOICES.read_text()) or {}
    return data.get("voices") or {}


def _load_patterns() -> dict[str, dict]:
    """Per-person behavioral patterns — 07_compile_patterns output."""
    if not PATTERNS.exists():
        return {}
    data = yaml.safe_load(PATTERNS.read_text()) or {}
    return data.get("patterns") or {}


def _det01(*parts) -> float:
    """Deterministic 0.0–1.0 from string parts; for attendance / skip decisions."""
    return _hash_int(*[str(p) for p in parts], n=10_000) / 10_000.0


# Drift candidates: 2 people whose msg_hour_peak gradually shifts later over
# time — the classic late-night burnout creep Fyralis should detect. Picked
# deterministically (no LLM); chosen to be people with already-late peaks +
# high commit volume.
BURNOUT_CANDIDATES = {"person:Rajil1213", "person:MdTeach"}
BURNOUT_START = date(2024, 6, 1)   # before this date, no drift
BURNOUT_PEAK_SHIFT_PER_YEAR = 1.8  # hours later per year


def _burnout_drift_hours(person_id: str | None, when: datetime) -> float:
    if person_id not in BURNOUT_CANDIDATES:
        return 0.0
    days = (when.date() - BURNOUT_START).days
    if days <= 0:
        return 0.0
    return (days / 365.0) * BURNOUT_PEAK_SHIFT_PER_YEAR


def _onboarding_volume_factor(person_id: str | None, when: datetime, facts: dict) -> float:
    """During the first 90 days after start, message volume scales up linearly
    from 0.3 → 1.0 — classic onboarding curve. Detected by Fyralis as a
    new-hire integration arc."""
    if not person_id:
        return 1.0
    person = next((p for p in facts["people"] if p["id"] == person_id), None)
    if not person:
        return 1.0
    try:
        start = date.fromisoformat(person["started_at"])
    except Exception:
        return 1.0
    days = (when.date() - start).days
    if days < 0:
        return 0.0     # hasn't started yet
    if days >= 90:
        return 1.0
    return 0.3 + (days / 90.0) * 0.7


def _skew_to_peak(when: datetime, person_id: str | None) -> datetime:
    """Pull a timestamp toward a person's active-hours peak, with burnout drift."""
    pat = _PATTERNS.get(person_id or "", {})
    peak = pat.get("msg_hour_peak", 14)
    spread = pat.get("msg_hour_spread", 3.0)
    # Burnout drift: gradually shift peak later over time for select people.
    peak = peak + _burnout_drift_hours(person_id, when)
    # Mix two hash-derived 0-1 numbers; sum-of-two roughly approximates Gaussian.
    u1 = _det01(person_id, when.toordinal(), when.minute, "h1")
    u2 = _det01(person_id, when.toordinal(), when.minute, "h2")
    offset = (u1 + u2 - 1.0) * spread * 2          # ~mean 0, std ~spread
    hr = int(round(peak + offset)) % 24
    return when.replace(hour=hr, minute=int(u1 * 59), second=int(u2 * 59))


def _resolve_handle(corpus_id: str | None, facts: dict) -> str:
    """`person:storopoli` → `storopoli` (or fall back to `team`)."""
    if not corpus_id:
        return "team"
    if corpus_id.startswith("person:"):
        return corpus_id.split(":", 1)[1]
    return corpus_id


_VOICES: dict[str, dict] = {}        # module-level caches; populated in main()
_PATTERNS: dict[str, dict] = {}
_CHATTER: dict[str, dict] = {}
_OFFICE: dict = {}                   # pto / external_events / conference_travel
_MANAGER_OF: dict[str, str] = {}     # corpus_id → manager corpus_id (1:1 pair edges)
_PEOPLE_BY_TEAM: dict[str, list[str]] = {}   # team_id → [person_id...]


# Emoji catalog: light positive, light negative, neutral acknowledgments. The
# distribution biases positive because real Slack reactions skew positive.
EMOJI_POSITIVE = ["+1", "rocket", "tada", "100", "fire", "white_check_mark", "muscle", "raised_hands", "heart"]
EMOJI_LIGHT_NEG = ["thinking_face", "eyes", "warning", "face_with_monocle", "skull"]
EMOJI_NEUTRAL = ["eyes", "thinking_face", "wave", "raised_hand", "saluting_face"]


def _is_dm_heavy(thread_id: str) -> bool:
    """~30% of threads have most of the discussion in private DMs — only the
    significant moments (kickoff, ship, postmortem) bubble up to the channel.
    A real common pattern Fyralis should detect as a 'low-channel-density'
    thread that still reaches its milestones."""
    return _hash_int(thread_id, "dm-heavy", n=10) < 3


def _build_manager_map(facts: dict) -> None:
    """Derive manager↔report pairs deterministically from team membership.

    - delbonis (cofounder) is the root of the tree.
    - Each non-ops team's highest-commit IC is the team lead → reports to delbonis.
    - team:ops lead reports to delbonis; other ops people report to ops lead.
    - All other team members report to their team lead.
    """
    global _MANAGER_OF, _PEOPLE_BY_TEAM
    _PEOPLE_BY_TEAM = {}
    for p in facts["people"]:
        _PEOPLE_BY_TEAM.setdefault(p["team"], []).append(p["id"])

    leads: dict[str, str] = {}
    for team_id, members in _PEOPLE_BY_TEAM.items():
        if team_id in (None, "needs:review"):
            continue
        # Pick the lead: highest-commit member, breaking ties by earliest start.
        def lead_score(person_id: str) -> tuple:
            p = next((x for x in facts["people"] if x["id"] == person_id), None)
            return (p["commits"] if p else 0, p["started_at"] or "9999-12-31")
        leads[team_id] = max(members, key=lead_score)

    _MANAGER_OF = {}
    cofounder = "person:delbonis"
    for team_id, members in _PEOPLE_BY_TEAM.items():
        lead = leads.get(team_id)
        for pid in members:
            if pid == cofounder:
                continue
            if pid == lead:
                _MANAGER_OF[pid] = cofounder        # team leads report to cofounder
            elif lead:
                _MANAGER_OF[pid] = lead


def _pick_reactions(seed: tuple, mood: str = "neutral") -> list[dict]:
    """Pick 0-3 emoji reactions for a message. Returns the Slack-shaped JSON."""
    n = _hash_int(*[str(s) for s in seed], "n_reactions", n=10)
    if n < 6:
        return []
    count = min(3, max(1, n - 6))
    pool = (EMOJI_POSITIVE if mood == "positive"
            else EMOJI_LIGHT_NEG if mood == "negative"
            else EMOJI_NEUTRAL + EMOJI_POSITIVE)
    chosen = []
    for i in range(count):
        emoji = pool[_hash_int(*[str(s) for s in seed], "emoji", str(i), n=len(pool))]
        if emoji not in [c["name"] for c in chosen]:
            chosen.append({"name": emoji, "count": 1 + _hash_int(*[str(s) for s in seed], emoji, n=4)})
    return chosen


def _is_departed(person_id: str | None, when: datetime, facts: dict) -> bool:
    """Has this person already left the company?"""
    if not person_id:
        return False
    person = next((p for p in facts["people"] if p["id"] == person_id), None)
    if not person:
        return False
    end = person.get("ended_at")
    if not end:
        return False
    try:
        return when.date() >= date.fromisoformat(end)
    except Exception:
        return False


def _person_id_for_login(facts: dict, login: str | None) -> str | None:
    if not login:
        return None
    for p in facts["people"]:
        if p["github_handle"].lower() == login.lower():
            return p["id"]
    return None


def _candidate_reviewers(facts: dict, author_pid: str) -> list[str]:
    """Reviewer pool for a PR author: prefer same-team, then adjacent teams.
    Sorted deterministically so the same PR always sees the same reviewer set."""
    author = next((p for p in facts["people"] if p["id"] == author_pid), None)
    if not author:
        return []
    team = author["team"]
    same = [pid for pid in _PEOPLE_BY_TEAM.get(team, []) if pid != author_pid]
    other = [pid for t, members in _PEOPLE_BY_TEAM.items() for pid in members
             if t != team and pid != author_pid]
    # Sort same-team by descending commit count; others by random-stable hash.
    by_id = {p["id"]: p for p in facts["people"]}
    same.sort(key=lambda pid: -by_id[pid]["commits"])
    other.sort(key=lambda pid: _hash_int(pid, "rev", n=1000))
    return same[:4] + other[:3]


def _synthetic_pr_reviews(*, repo_id: str, pr_number: int, author_pid: str,
                          opened_at: str, closed_at: str | None,
                          repo_name: str, facts: dict) -> Iterator[dict]:
    """Emit 0-2 review submissions on a PR. Deterministic from (repo, number).

    State distribution: approved 70%, commented 22%, changes_requested 8%.
    Reviewer thoroughness skews state — high thorough → more changes_requested,
    low thorough → more lgtm. Comment text drawn from reviewer's voice.review.
    Timestamp = opened_at + review_response_hours of reviewer.
    """
    seed = f"{repo_id}:{pr_number}"
    n_reviews = _hash_int(seed, "n", n=10)
    if n_reviews < 4:
        return  # 40% of PRs get no review (small / draft / self-merge / docs)
    n_reviews = 1 if n_reviews < 8 else 2
    candidates = _candidate_reviewers(facts, author_pid)
    if not candidates:
        return
    try:
        t_open = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
        t_close = (datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
                   if closed_at else None)
    except Exception:
        return
    for i in range(n_reviews):
        reviewer = candidates[_hash_int(seed, "rev", str(i), n=len(candidates))]
        pat = _PATTERNS.get(reviewer, {})
        lag_hours = pat.get("review_response_hours", 12.0)
        thoroughness = pat.get("review_thoroughness", 0.8)
        t_review = t_open + timedelta(hours=lag_hours + i * 18)
        if t_close and t_review > t_close:
            t_review = t_close - timedelta(minutes=10)
        if _is_on_pto(reviewer, t_review) or _is_departed(reviewer, t_review, facts):
            continue
        state_roll = _hash_int(seed, "state", str(i), n=100) / 100.0
        state_roll *= (1.4 - thoroughness)  # thorough reviewers shift toward changes_requested
        state = ("approved" if state_roll < 0.70
                 else "commented" if state_roll < 0.92
                 else "changes_requested")
        review_pool = (_VOICES.get(reviewer, {})
                       .get("snippets", {}).get("review") or [])
        body = (review_pool[_hash_int(seed, "body", str(i), n=len(review_pool))]
                if review_pool else "lgtm")
        yield {
            "t": _iso(t_review),
            "provider": "github", "kind": "review.submit",
            "actor": f"ghuser:{_resolve_handle(reviewer, facts)}",
            "payload": {"repo": repo_id, "pr_number": pr_number,
                        "state": state, "body": body[:500],
                        "reviewer": reviewer,
                        "lag_hours": round(lag_hours, 1)},
        }


def _pre_departure_factor(person_id: str | None, when: datetime, facts: dict) -> float:
    """For people about to leave, activity tapers in the 6 weeks beforehand.
    Returns a 0..1 multiplier on their message emission probability."""
    if not person_id:
        return 1.0
    person = next((p for p in facts["people"] if p["id"] == person_id), None)
    if not person or not person.get("ended_at"):
        return 1.0
    try:
        end = date.fromisoformat(person["ended_at"])
    except Exception:
        return 1.0
    days_until = (end - when.date()).days
    if days_until > 42 or days_until < 0:
        return 1.0
    # Linear decline from 1.0 at 42 days out to 0.3 at end date.
    return 0.3 + (days_until / 42.0) * 0.7


def _load_office_life() -> dict:
    if not OFFICE_LIFE.exists():
        return {}
    return yaml.safe_load(OFFICE_LIFE.read_text()) or {}


def _load_chatter() -> dict[str, dict]:
    if not CHATTER.exists():
        return {}
    data = yaml.safe_load(CHATTER.read_text()) or {}
    return data.get("chatter_by_person") or {}


def _is_on_pto(person_id: str | None, when: datetime) -> bool:
    """Is this person off on this date (vacation / sick / conference travel)?"""
    if not person_id:
        return False
    d = when.date()
    for w in (_OFFICE.get("pto") or {}).get(person_id, []):
        try:
            s = date.fromisoformat(w["start"])
            e = date.fromisoformat(w["end"])
            if s <= d <= e:
                return True
        except Exception:
            continue
    # Conference travel.
    for ev in _OFFICE.get("external_events", []):
        if ev.get("kind") != "conference":
            continue
        if person_id not in (_OFFICE.get("conference_travel") or {}).get(ev["label"], []):
            continue
        s = date.fromisoformat(ev["date"])
        e = s + timedelta(days=ev.get("days", 5))
        if s <= d <= e:
            return True
    return False


def thread_events(thread: dict, facts: dict) -> Iterator[dict]:
    """Render one thread spec into provider events."""
    channel_id = _slack_channel_for(thread["id"])
    yield {"t": _iso(_midday(thread["window"]["start"])),
           "provider": "slack", "kind": "channel.create",
           "actor": thread.get("driver"),
           "payload": {"id": channel_id, "name": channel_id.split(":", 1)[1],
                       "is_private": False}}

    # One Jira epic per thread.
    epic_key = f"STR-{_hash_int(thread['id'], n=8999) + 1000}"
    yield {"t": _iso(_midday(thread["window"]["start"], hour=9)),
           "provider": "jira", "kind": "issue.create",
           "actor": thread.get("driver"),
           "payload": {"key": epic_key, "project": "STR",
                       "type": "Epic", "summary": thread["title"],
                       "reporter": thread.get("driver"),
                       "assignee": thread.get("driver")}}

    cast = thread.get("cast") or []

    # Each beat: scatter ~6-15 slack msgs across its window, plus a jira story
    # and (where relevant) a notion page linking to the artifact.
    artifacts = {a.get("beat"): a for a in thread.get("artifacts") or []}
    for bi, beat in enumerate(thread.get("beats") or []):
        bid = beat.get("id", f"B{bi+1}")
        start = _midday(beat["start"], hour=9 + (bi % 6))
        end = _midday(beat.get("end", beat["start"]), hour=17)
        days = max(1, (end - start).days)
        participants = beat.get("participants") or cast[: max(2, len(cast) // 2)]
        tensions = beat.get("tensions") or thread.get("tensions") or ["—"]

        # Jira story for the beat — now carries estimate (story_points) and a
        # planned cycle. Actuals get computed at Done transition below; the
        # gap between estimate and actuals is per-person calibration signal.
        beat_kind = beat.get("kind", "impl")
        story_key = f"STR-{_hash_int(thread['id'], bid, n=8999) + 2000}"
        assignee_p = participants[0] if participants else thread.get("driver")
        # Estimate based on beat scope; jitter per assignee's "optimism".
        base_pts = {"kickoff": 1, "design": 3, "impl": 5, "review": 2,
                    "audit": 5, "ship": 2, "postmortem": 1, "retro": 1,
                    "hiring": 2}.get(beat_kind, 3)
        # Optimistic assignees underestimate; chronic slippers over-promise.
        optimism = (_PATTERNS.get(assignee_p or "", {}).get("ship_lag_hours", 0) or 0) / 48.0
        estimate_pts = max(1, round(base_pts * (1.0 - max(min(optimism, 0.4), -0.2))))
        yield {"t": _iso(start), "provider": "jira", "kind": "issue.create",
               "actor": assignee_p,
               "payload": {"key": story_key, "project": "STR",
                           "type": "Story", "epic": epic_key,
                           "summary": (beat.get("summary") or beat_kind)[:200],
                           "reporter": assignee_p,
                           "assignee": assignee_p,
                           "story_points": estimate_pts,
                           "labels": [thread["id"].lower(), beat_kind]}}
        # Transition midway and close at end. The "Done" transition is shifted
        # by the assignee's ship_lag pattern + any PTO that lands in the beat
        # window — Fyralis should see chronic slippers, leave-stalls, and
        # ahead-of-plan shippers as distinct structural signals.
        assignee = participants[0] if participants else None
        mid = start + timedelta(days=max(1, days // 2))
        yield {"t": _iso(mid), "provider": "jira", "kind": "issue.transition",
               "actor": assignee,
               "payload": {"key": story_key, "from_status": "To Do",
                           "to_status": "In Progress"}}
        planned_done = end - timedelta(hours=1)
        ship_lag = _PATTERNS.get(assignee or "", {}).get("ship_lag_hours", 0.0)
        # Add up PTO days the assignee took during the beat window — story sits idle.
        pto_days = 0
        if assignee:
            day = start.date()
            while day <= end.date():
                if _is_on_pto(assignee, datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)):
                    pto_days += 1
                day += timedelta(days=1)
        actual_done = planned_done + timedelta(hours=ship_lag) + timedelta(days=pto_days)
        # Never go before the In Progress transition.
        actual_done = max(actual_done, mid + timedelta(hours=2))
        # Stale tickets: ~10% of stories never get a Done transition — they
        # sit in In Progress until they rot in the backlog. Classic backlog
        # rot pattern. Audit/postmortem beats always close (compliance).
        stale = (_det01(thread["id"], bid, "stale") < 0.10
                 and beat_kind not in ("audit", "postmortem", "ship"))
        if not stale:
            yield {"t": _iso(actual_done),
                   "provider": "jira", "kind": "issue.transition",
                   "actor": assignee,
                   "payload": {"key": story_key, "from_status": "In Progress",
                               "to_status": "Done",
                               "planned_end": _iso(planned_done),
                               "lag_hours": round(ship_lag, 1),
                               "stalled_days_pto": pto_days}}

        # Notion page for high-signal beats (uses the LLM artifact if present).
        if bid in artifacts:
            art = artifacts[bid]
            page_id = f"notion:{thread['id']}:{bid}"
            art_path = ARTIFACTS / f"{thread['id']}__{bid}__{_slug(art.get('title',''))}.md"
            body = art_path.read_text() if art_path.exists() else f"# {art.get('title','Untitled')}\n\n(artifact pending generation)\n"
            yield {"t": _iso(start + timedelta(hours=4)),
                   "provider": "notion", "kind": "page.create",
                   "actor": art.get("author") or thread.get("driver"),
                   "payload": {"id": page_id, "title": art.get("title", "Untitled"),
                               "kind": art.get("kind", "doc"),
                               "body_md": body}}

            # Action-item flow: high-signal artifacts (RFCs, postmortems,
            # design docs) spawn 2-4 follow-up Jira tickets in the days after.
            # Most close 1-3 weeks later; ~12% become stale (backlog rot).
            # Fyralis can detect this as the doc → action → resolution arc.
            if art.get("kind") in ("rfc", "design_doc", "postmortem", "retro"):
                n_followups = 2 + _hash_int(thread["id"], bid, "fup_n", n=3)
                for fi in range(n_followups):
                    delay_days = 1 + _hash_int(thread["id"], bid, str(fi), "delay", n=6)
                    fup_when = start + timedelta(hours=8 + delay_days * 24)
                    fup_assignee = cast[fi % len(cast)] if cast else assignee_p
                    fup_key = f"STR-{_hash_int(thread['id'], bid, 'fup', str(fi), n=8999) + 5000}"
                    yield {"t": _iso(fup_when), "provider": "jira",
                           "kind": "issue.create",
                           "actor": fup_assignee,
                           "payload": {"key": fup_key, "project": "STR",
                                       "type": "Story", "epic": epic_key,
                                       "summary": f"Follow-up: {art.get('title','RFC')[:140]}",
                                       "reporter": fup_assignee,
                                       "assignee": fup_assignee,
                                       "story_points": 2,
                                       "labels": [thread["id"].lower(),
                                                  "follow-up", art.get("kind","rfc")],
                                       "linked_rfc": page_id,
                                       # Dependency graph: follow-ups depend on
                                       # the parent epic / originating beat story.
                                       "issue_links": [
                                           {"type": "Relates",   "key": epic_key},
                                           {"type": "Depends on", "key": story_key},
                                       ]}}
                    # Reassignment chain: ~15% of follow-ups bounce 1-2 times
                    # before settling. Detected as "tickets reassigned ≥2x have
                    # higher slip rates" / "X always tries to deflect tickets to Y".
                    if _det01(fup_key, "reassign") < 0.15 and len(cast) > 1:
                        new_assignee = cast[(fi + 2) % len(cast)]
                        rh_when = fup_when + timedelta(days=1 + _hash_int(fup_key, "rh", n=3))
                        yield {"t": _iso(rh_when), "provider": "jira",
                               "kind": "issue.assign",
                               "actor": fup_assignee,
                               "payload": {"key": fup_key,
                                           "from_assignee": fup_assignee,
                                           "to_assignee": new_assignee}}
                    # In Progress transition a few days after creation.
                    ip_when = fup_when + timedelta(days=2 + _hash_int(fup_key, "ip", n=4))
                    yield {"t": _iso(ip_when), "provider": "jira",
                           "kind": "issue.transition",
                           "actor": fup_assignee,
                           "payload": {"key": fup_key, "from_status": "To Do",
                                       "to_status": "In Progress"}}
                    # Done transition 1-3 weeks after, unless stale (~12% rot).
                    if _det01(fup_key, "stale") > 0.12:
                        close_when = fup_when + timedelta(days=7 + _hash_int(fup_key, "close", n=14))
                        yield {"t": _iso(close_when), "provider": "jira",
                               "kind": "issue.transition",
                               "actor": fup_assignee,
                               "payload": {"key": fup_key,
                                           "from_status": "In Progress",
                                           "to_status": "Done"}}

        # Slack chatter across the beat — voice-aware: each person draws from
        # their own snippet pool (from voices.yaml) for the matching beat-kind.
        # Falls back to the global template bank when a person has no voice yet.
        kind = beat.get("kind", "impl")
        fallback = BEAT_SLACK.get(kind, BEAT_SLACK["impl"])
        msg_count = min(15, max(5, days * 2))

        # DM-shadow: ~30% of threads have most of the work-discussion in
        # private DMs; only kickoff/ship/postmortem moments surface in the
        # channel. Fyralis should detect this as a "low channel-density"
        # thread that still reaches its milestones.
        dm_heavy = _is_dm_heavy(thread["id"])
        if dm_heavy and kind not in ("kickoff", "ship", "postmortem", "all_hands_recap"):
            msg_count = max(2, msg_count // 4)
        # Remember the first top-level message per beat — used as thread_ts
        # anchor for any threaded replies inside this beat window.
        anchor_ts: str | None = None
        for m in range(msg_count):
            actor = participants[m % len(participants)] if participants else None
            offset = (days * 86400 * (m + 1)) // (msg_count + 1)
            when = start + timedelta(seconds=offset + (m % 7) * 600)
            # Skew message timestamp toward this person's active-hours peak.
            when = _skew_to_peak(when, actor)
            # Drop weekend messages probabilistically per their weekend_factor.
            if when.weekday() >= 5:
                factor = _PATTERNS.get(actor or "", {}).get("weekend_msg_factor", 0.4)
                if _det01(actor, when.toordinal(), m, "wknd") > factor:
                    continue

            # Skip work messages while the actor is on PTO / at conference,
            # or already departed, or in their pre-departure decline window.
            if _is_on_pto(actor, when) or _is_departed(actor, when, facts):
                continue
            pre_dep = _pre_departure_factor(actor, when, facts)
            if pre_dep < 1.0 and _det01(actor, when.toordinal(), m, "predep") > pre_dep:
                continue
            # Onboarding ramp: new hires post less in their first 90 days.
            onboard = _onboarding_volume_factor(actor, when, facts)
            if onboard < 1.0 and _det01(actor, when.toordinal(), m, "onb") > onboard:
                continue

            # Per-person snippet pool keyed by beat-kind. Same person under
            # the same (thread, beat, m) seed always yields the same line —
            # deterministic, replayable.
            pool = (_VOICES.get(actor or "", {})
                    .get("snippets", {})
                    .get(kind, [])) or fallback
            text = _pick(pool, thread["id"], bid, str(m))

            # Resolve any corpus_id literals that might still appear in templates.
            other = (participants[(m + 1) % len(participants)]
                     if participants and len(participants) > 1 else actor)
            text = text.format(
                summary=(beat.get("summary") or "")[:80],
                tension=(tensions[m % len(tensions)] or "")[:80],
                participant=_resolve_handle(other, facts),
                artifact_title=(artifacts.get(bid) or {}).get("title", "the doc"),
                idx=(m % 9) + 1,
            ) if "{" in text else text

            # ~12% of work-channel messages are actually off-topic chatter —
            # someone shares a coffee meme mid-impl. Keeps work channels human.
            if _det01(actor, thread["id"], bid, m, "offtopic") < 0.12:
                ch_pool = (_CHATTER.get(actor or "", {}).get("random") or [])
                if ch_pool:
                    text = _pick(ch_pool, actor, thread["id"], bid, m, "chat")

            # Slack threading: first message of the beat is the anchor; some
            # subsequent messages reply to the anchor instead of going to the
            # top of the channel. RFCs/audits/postmortems get deeper threads.
            ts_str = slack_ts(when) if "slack_ts" in globals() else None
            top_level = (anchor_ts is None) or (
                _det01(actor, thread["id"], bid, m, "tl") > {
                    "design": 0.45, "audit": 0.40, "postmortem": 0.40,
                    "retro": 0.55,
                }.get(kind, 0.70)
            ) and not top_level if False else None  # always set fresh below
            top_level = anchor_ts is None or (
                _det01(actor, thread["id"], bid, m, "tl") > {
                    "design": 0.40, "audit": 0.35, "postmortem": 0.35,
                    "retro": 0.50,
                }.get(kind, 0.65)
            )
            # Wrong-channel posts: ~2% of work content accidentally lands in #random.
            target_channel = channel_id
            if _det01(actor, thread["id"], bid, m, "wrong-ch") < 0.02:
                target_channel = RANDOM_CHANNEL
            payload: dict = {"channel": target_channel, "text": text,
                             "thread_anchor": bid}
            if top_level:
                anchor_ts = _iso(when)
            else:
                payload["thread_ts"] = anchor_ts

            # Emoji reactions on top-level messages — sentiment + engagement
            # signal. RFC/design beats skew higher reaction rates.
            if top_level:
                react_seed = (actor or "", thread["id"], bid, m)
                mood = ("positive" if kind in ("ship", "kickoff")
                        else "negative" if kind in ("audit", "postmortem")
                        else "neutral")
                rxn = _pick_reactions(react_seed, mood=mood)
                if rxn:
                    payload["reactions"] = rxn

            yield {"t": _iso(when), "provider": "slack", "kind": "message",
                   "actor": actor,
                   "payload": payload}

        # Calendar event for kickoff and ship beats.
        if kind in ("kickoff", "ship", "audit", "all_hands_recap"):
            yield {"t": _iso(start), "provider": "calendar", "kind": "event.create",
                   "actor": thread.get("driver"),
                   "payload": {"id": f"cal:{thread['id']}:{bid}",
                               "summary": f"{thread['title']} — {kind}",
                               "start": _iso(start), "end": _iso(start + timedelta(hours=1)),
                               "attendees": [a for a in (participants or cast)[:8] if a]}}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "untitled").lower()).strip("-")[:60] or "untitled"


# -----------------------------------------------------------------------------
# Office-life chatter — non-work Slack volume
# -----------------------------------------------------------------------------

RANDOM_CHANNEL = "channel:random"


def chatter_events(facts: dict, start: str, end: str) -> Iterator[dict]:
    """The non-work side: #random chatter, PTO announces, conference posts,
    incident reactions. Everything that makes a team feel like humans."""
    d0 = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    d1 = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)

    # Create #random the day the company is founded.
    yield {"t": _iso(_midday(d0.date(), hour=9)), "provider": "slack",
           "kind": "channel.create",
           "payload": {"id": RANDOM_CHANNEL, "name": "random", "is_private": False}}

    people = [p for p in facts["people"]]

    # 1) Daily #random posts: a few people drop a message most weekdays.
    cur = d0
    while cur < d1:
        # 2-5 people post in #random per day, deterministically picked.
        picks = sorted(people, key=lambda p: _hash_int(p["id"], cur.toordinal(), "rnd"))
        count = 2 + _hash_int("count", cur.toordinal(), n=4)
        for p in picks[:count]:
            pid = p["id"]
            if _is_on_pto(pid, cur):
                continue
            ch = _CHATTER.get(pid, {})
            # Mix sources by day-of-week: weekend → weekend_chatter, etc.
            if cur.weekday() >= 5:
                pool = ch.get("weekend_chatter") or ch.get("random") or []
                if not pool: continue
                # Apply weekend factor for chatter too.
                wf = _PATTERNS.get(pid, {}).get("weekend_msg_factor", 0.4)
                if _det01(pid, cur.toordinal(), "wknd-rnd") > wf:
                    continue
            else:
                pool = ch.get("random") or []
                if not pool: continue
            text = _pick(pool, pid, cur.toordinal())
            when = _skew_to_peak(_midday(cur.date(), hour=12), pid)
            yield {"t": _iso(when), "provider": "slack", "kind": "message",
                   "actor": pid,
                   "payload": {"channel": RANDOM_CHANNEL, "text": text,
                               "category": "chatter:random"}}
        cur += timedelta(days=1)

    # 2) Reactions sprinkled in #random (acknowledge each other's posts).
    cur = d0
    while cur < d1:
        reacting = sorted(people, key=lambda p: _hash_int(p["id"], cur.toordinal(), "react"))
        for p in reacting[: 1 + _hash_int("rn", cur.toordinal(), n=3)]:
            pid = p["id"]
            if _is_on_pto(pid, cur):
                continue
            pool = _CHATTER.get(pid, {}).get("reactions") or []
            if not pool: continue
            text = _pick(pool, pid, cur.toordinal(), "react")
            when = _skew_to_peak(_midday(cur.date(), hour=15), pid)
            yield {"t": _iso(when + timedelta(minutes=11)), "provider": "slack",
                   "kind": "message", "actor": pid,
                   "payload": {"channel": RANDOM_CHANNEL, "text": text,
                               "category": "chatter:reaction"}}
        cur += timedelta(days=1)

    # 3) PTO announces: 2-3 days before each PTO window.
    for pid, windows in (_OFFICE.get("pto") or {}).items():
        ch = _CHATTER.get(pid, {})
        announce_pool = ch.get("pto_announce") or []
        return_pool = ch.get("return_from_pto") or []
        for w in windows:
            if w["kind"] not in ("vacation",):  # skip sick days (no advance notice)
                continue
            try:
                s = datetime.fromisoformat(w["start"]).replace(tzinfo=timezone.utc)
                e = datetime.fromisoformat(w["end"]).replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if s < d0 or e > d1:
                continue
            # Announce 2-3 days before
            when_a = _skew_to_peak(s - timedelta(days=2 + _hash_int(pid, w["start"], n=2)),
                                   pid)
            if announce_pool:
                yield {"t": _iso(when_a), "provider": "slack", "kind": "message",
                       "actor": pid,
                       "payload": {"channel": RANDOM_CHANNEL,
                                   "text": _pick(announce_pool, pid, w["start"]),
                                   "category": "chatter:pto_announce"}}
            # Return-from-PTO post, morning after
            when_r = _skew_to_peak(e + timedelta(days=1), pid)
            if return_pool:
                yield {"t": _iso(when_r), "provider": "slack", "kind": "message",
                       "actor": pid,
                       "payload": {"channel": RANDOM_CHANNEL,
                                   "text": _pick(return_pool, pid, w["start"], "ret"),
                                   "category": "chatter:return"}}

    # 4) External events: incident reactions, conference posts, news takes.
    for ev in _OFFICE.get("external_events", []):
        try:
            evd = datetime.fromisoformat(ev["date"]).replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if evd < d0 or evd > d1:
            continue
        days = ev.get("days", 1)
        kind = ev["kind"]
        # Pick the right chatter category per event kind.
        chatter_cat = {
            "disclosure":     "incident_reaction",
            "incident":       "incident_reaction",
            "conference":     "conference_post",
            "milestone":      "news_take",
            "external_press": "news_take",
            "strategic_round":"news_take",
            "halving":        "news_take",
        }.get(kind, "random")

        for day_offset in range(min(days, 4)):
            when_day = evd + timedelta(days=day_offset)
            # 4-8 people react
            reactors = sorted(people, key=lambda p: _hash_int(p["id"], ev["date"], "ext"))
            for p in reactors[: 4 + _hash_int("rxc", ev["date"], n=5)]:
                pid = p["id"]
                if _is_on_pto(pid, when_day):
                    continue
                # Conference posts only from travelers
                if kind == "conference":
                    if pid not in (_OFFICE.get("conference_travel") or {}).get(ev["label"], []):
                        continue
                ch = _CHATTER.get(pid, {})
                pool = ch.get(chatter_cat) or ch.get("random") or []
                if not pool: continue
                text = _pick(pool, pid, ev["date"], day_offset)
                when = _skew_to_peak(_midday(when_day.date(), hour=11), pid)
                yield {"t": _iso(when + timedelta(minutes=day_offset * 23)),
                       "provider": "slack", "kind": "message", "actor": pid,
                       "payload": {"channel": RANDOM_CHANNEL, "text": text,
                                   "category": f"chatter:{chatter_cat}",
                                   "linked_event": ev["label"]}}


# -----------------------------------------------------------------------------
# Recurring rituals
# -----------------------------------------------------------------------------

def ritual_events(facts: dict, start: str, end: str) -> Iterator[dict]:
    """Weekly standup, monthly all-hands. Cheap, high-volume calendar signal."""
    d0 = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    d1 = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    teams = facts.get("teams_template", [])
    people_by_team: dict[str, list[str]] = {}
    for p in facts["people"]:
        people_by_team.setdefault(p["team"], []).append(p["id"])

    # Weekly standups per team (Monday 9am). Attendees filtered by each
    # person's standup_attendance pattern — Fyralis should see who shows up.
    cur = d0
    while cur < d1:
        if cur.weekday() == 0:  # Monday
            for team in teams:
                members = people_by_team.get(team["id"], [])[:8]
                if not members:
                    continue
                attending = [
                    pid for pid in members
                    if _det01(pid, cur.toordinal(), "standup")
                       < _PATTERNS.get(pid, {}).get("standup_attendance", 0.85)
                ]
                if not attending:
                    continue
                when = cur.replace(hour=9, minute=0)
                yield {"t": _iso(when), "provider": "calendar", "kind": "event.create",
                       "payload": {"id": f"cal:standup:{team['id']}:{cur.date()}",
                                   "summary": f"{team['name']} standup",
                                   "start": _iso(when),
                                   "end": _iso(when + timedelta(minutes=15)),
                                   "attendees": attending,
                                   "invited": members,
                                   "recurring": True}}
        cur += timedelta(days=1)

    # Monthly all-hands (first Friday).
    cur = d0
    while cur < d1:
        if cur.weekday() == 4 and cur.day <= 7:
            attendees = [p["id"] for p in facts["people"]]
            when = cur.replace(hour=16, minute=0)
            yield {"t": _iso(when), "provider": "calendar", "kind": "event.create",
                   "payload": {"id": f"cal:allhands:{cur.date()}",
                               "summary": "All hands",
                               "start": _iso(when),
                               "end": _iso(when + timedelta(hours=1)),
                               "attendees": attendees, "recurring": False}}
        cur += timedelta(days=1)


# -----------------------------------------------------------------------------
# 1:1 meetings + private Notion notes
# -----------------------------------------------------------------------------

def one_on_one_events(facts: dict, start: str, end: str) -> Iterator[dict]:
    """Weekly 1:1 between every manager↔report pair.

    Each 1:1 emits:
      - a calendar.event.create (Tuesday slot, per-pair deterministic hour)
      - a private notion.page.create with templated notes drawn from voice
        and the report's recent work

    These are the highest-density manager-report cadence signal in the corpus.
    A model layer can detect 1:1 cadence drops, frequent cancellations (no
    page for that week → reschedule), and topic drift across the year.
    """
    d0 = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    d1 = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)

    pairs = list(_MANAGER_OF.items())              # [(report, manager), ...]
    if not pairs:
        return

    person_by_id = {p["id"]: p for p in facts["people"]}

    cur = d0
    while cur < d1:
        if cur.weekday() != 1:                     # Tuesdays only
            cur += timedelta(days=1)
            continue
        for report, manager in pairs:
            rp = person_by_id.get(report)
            mp = person_by_id.get(manager)
            if not rp or not mp:
                continue
            # Skip if either person hasn't started yet or has departed.
            if rp.get("started_at") and date.fromisoformat(rp["started_at"]) > cur.date():
                continue
            if _is_departed(report, cur, facts) or _is_departed(manager, cur, facts):
                continue
            # 12% chance the 1:1 is canceled (PTO of either, conflicting meeting).
            if _is_on_pto(report, cur) or _is_on_pto(manager, cur):
                continue
            if _det01(report, manager, cur.toordinal(), "skip") < 0.12:
                continue
            # Deterministic per-pair slot: hour 10-15.
            slot_h = 10 + _hash_int(report, manager, "slot", n=6)
            when = cur.replace(hour=slot_h, minute=0, second=0, microsecond=0)
            yield {
                "t": _iso(when), "provider": "calendar", "kind": "event.create",
                "actor": manager,
                "payload": {
                    "id": f"cal:1on1:{manager.split(':')[1]}:{report.split(':')[1]}:{cur.date()}",
                    "summary": f"{_resolve_handle(manager, facts)} <> {_resolve_handle(report, facts)} 1:1",
                    "start": _iso(when),
                    "end": _iso(when + timedelta(minutes=30)),
                    "attendees": [manager, report],
                    "recurring": True,
                    "category": "1on1",
                },
            }
            # Private Notion page with meeting notes.
            r_voice = _VOICES.get(report, {})
            r_concerns = r_voice.get("voice", {}).get("typical_concerns") or []
            picked_concern = (_pick(r_concerns, report, cur.toordinal(), "topic")
                              if r_concerns else "current sprint")
            action_pool = (_VOICES.get(report, {}).get("snippets", {}).get("review")
                           or _VOICES.get(report, {}).get("snippets", {}).get("impl") or [])
            action = (_pick(action_pool, report, cur.toordinal(), "act")
                      if action_pool else "follow up next week")
            body = (
                f"# 1:1 — {_resolve_handle(manager, facts)} & {_resolve_handle(report, facts)}\n"
                f"Date: {cur.date().isoformat()}\n\n"
                f"## What's top of mind for {_resolve_handle(report, facts)}\n"
                f"- {picked_concern}\n\n"
                f"## Action items\n"
                f"- [ ] {action}\n"
            )
            yield {
                "t": _iso(when + timedelta(minutes=35)),
                "provider": "notion", "kind": "page.create",
                "actor": manager,
                "payload": {
                    "id": f"notion:1on1:{manager.split(':')[1]}:{report.split(':')[1]}:{cur.date()}",
                    "title": f"1:1 — {_resolve_handle(manager, facts)} & {_resolve_handle(report, facts)} ({cur.date()})",
                    "kind": "1on1_note",
                    "body_md": body,
                    "is_private": True,
                    "audience": [manager, report],
                },
            }
        cur += timedelta(days=1)


# -----------------------------------------------------------------------------
# CI flakes + production hotfix incidents
# -----------------------------------------------------------------------------

CI_FLAKE_CHANNEL = "channel:ci-flakes"

CI_FLAKE_PATTERNS = [
    "ci flake again on {repo}: {test_name}",
    "{test_name} timing out on {repo} runner — retrying",
    "anyone else seeing {test_name} fail intermittently?",
    "rerun {test_name} got me through; rooted cause TBD",
    "this is the third time today {test_name} flakes",
    "macos runner OOM on {repo}/{test_name} — known?",
    "{test_name} times out only on the 4-core runner",
]
TEST_NAMES = [
    "test_bridge_deposit_flow", "test_proof_verifier_long",
    "integration_strata_e2e", "test_p2p_handshake",
    "test_reorg_recovery", "test_state_replay",
    "test_ssz_roundtrip", "test_groth16_verify_real",
    "test_circuit_compile_long", "test_witness_serialization",
]

PROD_INCIDENT_TEMPLATES = [
    {"summary": "Strata testnet reorg recovery", "severity": "P1",
     "description": "Testnet briefly diverged after Bitcoin reorg; bridge state machine needs manual reconciliation.",
     "channel_kind": "incident"},
    {"summary": "Glock verifier OOM on mainnet shadow", "severity": "P2",
     "description": "Memory growth on long-running verifier process; impacted shadow nodes only.",
     "channel_kind": "incident"},
    {"summary": "Bridge operator node syncing wedged", "severity": "P1",
     "description": "Operator nodes stuck at a stale checkpoint after a stale-sync incident.",
     "channel_kind": "incident"},
    {"summary": "Mainnet shadow proof generation regression", "severity": "P2",
     "description": "Proof gen latency jumped 4x after dependency bump.",
     "channel_kind": "incident"},
]


def ci_flake_events(facts: dict, start: str, end: str) -> Iterator[dict]:
    """Recurring CI noise + a handful of real production incidents.

    Real engineering teams complain about CI flakes ~weekly. They cluster
    around active development periods. Fyralis should detect:
      - "CI flake density spikes during testnet-launch windows"
      - "test_X is the top flake — owner candidate?"
      - "incidents cluster after dependency bumps"
    """
    d0 = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    d1 = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)

    # Create the #ci-flakes channel up front.
    yield {"t": _iso(_midday(d0.date(), hour=10)),
           "provider": "slack", "kind": "channel.create",
           "payload": {"id": CI_FLAKE_CHANNEL, "name": "ci-flakes",
                       "is_private": False}}

    # Pick infra people preferentially as posters; fall back to everyone.
    posters = [p["id"] for p in facts["people"]
               if p.get("team") in ("team:infra", "team:protocol", "team:ops")]

    # Weekly cadence with 2-5 messages per week.
    cur = d0
    week = 0
    while cur < d1:
        if cur.weekday() == 2:  # Wednesdays
            n_msgs = 2 + _hash_int("flake_n", week, n=4)
            for i in range(n_msgs):
                actor = posters[_hash_int("flake", week, str(i), n=len(posters))]
                if _is_on_pto(actor, cur) or _is_departed(actor, cur, facts):
                    continue
                tpl = CI_FLAKE_PATTERNS[_hash_int("flake_t", week, str(i),
                                                  n=len(CI_FLAKE_PATTERNS))]
                test_name = TEST_NAMES[_hash_int("flake_test", week, str(i),
                                                  n=len(TEST_NAMES))]
                # Bias to most-active repos.
                repo = ["alpen", "strata-bridge", "zkaleido", "bitcoind-async-client",
                        "alpen-dashboards", "mosaic"][_hash_int("flake_r", week, str(i), n=6)]
                text = tpl.format(repo=repo, test_name=test_name)
                when = _skew_to_peak(_midday(cur.date(), hour=11) + timedelta(minutes=i*13),
                                     actor)
                yield {"t": _iso(when), "provider": "slack", "kind": "message",
                       "actor": actor,
                       "payload": {"channel": CI_FLAKE_CHANNEL, "text": text,
                                   "category": "ci_flake"}}
        cur += timedelta(days=1)
        if cur.weekday() == 2:
            week += 1

    # Production incidents: one per ~3 months. Each is a burst of activity
    # across #ci-flakes + a Notion postmortem + a Jira fix ticket.
    n_incidents = max(1, ((d1 - d0).days // 90))
    for i in range(min(n_incidents, len(PROD_INCIDENT_TEMPLATES) * 3)):
        tpl = PROD_INCIDENT_TEMPLATES[i % len(PROD_INCIDENT_TEMPLATES)]
        when = d0 + timedelta(days=(i + 1) * 90 + _hash_int("inc", str(i), n=20))
        if when > d1:
            break
        # Pick an incident commander deterministically.
        commander = posters[_hash_int("inc_cmd", str(i), n=len(posters))]
        if _is_departed(commander, when, facts):
            continue
        # Slack burst: 4-7 messages in the first 2 hours, then 3-5 over the day.
        for hour_offset in (0, 1, 2, 4, 6, 8):
            poster = posters[_hash_int("inc_p", str(i), str(hour_offset),
                                        n=len(posters))]
            if _is_on_pto(poster, when) or _is_departed(poster, when, facts):
                continue
            msg_pool = (_CHATTER.get(poster, {}).get("incident_reaction") or
                        _CHATTER.get(poster, {}).get("random") or [])
            if not msg_pool:
                continue
            text = _pick(msg_pool, str(i), str(hour_offset), "inc")
            yield {"t": _iso(when + timedelta(hours=hour_offset)),
                   "provider": "slack", "kind": "message",
                   "actor": poster,
                   "payload": {"channel": CI_FLAKE_CHANNEL, "text": text,
                               "category": f"incident:{tpl['severity']}",
                               "linked_incident": tpl["summary"]}}
        # Notion postmortem 1-3 days after.
        post_when = when + timedelta(days=1 + _hash_int("inc_pm", str(i), n=3))
        yield {"t": _iso(post_when),
               "provider": "notion", "kind": "page.create",
               "actor": commander,
               "payload": {"id": f"notion:incident:{i}:{when.date()}",
                           "title": f"Postmortem — {tpl['summary']}",
                           "kind": "postmortem",
                           "body_md": (
                               f"# Postmortem — {tpl['summary']}\n\n"
                               f"**Severity:** {tpl['severity']}\n"
                               f"**Incident commander:** {_resolve_handle(commander, facts)}\n"
                               f"**Date:** {when.date()}\n\n"
                               f"## Summary\n{tpl['description']}\n\n"
                               f"## Timeline\n- Detected\n- Triaged\n- Mitigated\n- Resolved\n\n"
                               f"## Action items\n- [ ] Add monitoring\n- [ ] Add regression test\n"
                           ),
                           "category": "incident_postmortem"}}
        # Jira fix ticket.
        fix_key = f"STR-{_hash_int('inc', str(i), n=8999) + 9000}"
        yield {"t": _iso(when + timedelta(hours=2)),
               "provider": "jira", "kind": "issue.create",
               "actor": commander,
               "payload": {"key": fix_key, "project": "STR", "type": "Bug",
                           "summary": f"Fix: {tpl['summary']}",
                           "reporter": commander, "assignee": commander,
                           "story_points": 3,
                           "labels": ["incident", "hotfix", tpl["severity"]]}}
        # Close the fix ticket within a week.
        yield {"t": _iso(when + timedelta(days=2 + _hash_int("inc_close", str(i), n=5))),
               "provider": "jira", "kind": "issue.transition",
               "actor": commander,
               "payload": {"key": fix_key, "from_status": "To Do",
                           "to_status": "In Progress"}}
        yield {"t": _iso(when + timedelta(days=5 + _hash_int("inc_done", str(i), n=4))),
               "provider": "jira", "kind": "issue.transition",
               "actor": commander,
               "payload": {"key": fix_key, "from_status": "In Progress",
                           "to_status": "Done"}}


# -----------------------------------------------------------------------------
# Hiring pipeline (Tier 2.3)
# -----------------------------------------------------------------------------

HIRING_CHANNEL = "channel:hiring-loops"

CANDIDATE_POOL = [
    ("Sofia Park", "protocol", 6),
    ("Marcus Chen", "research", 12),
    ("Priya Iyer", "bridge", 5),
    ("Tomáš Novak", "infra", 4),
    ("Ana López", "research", 10),
    ("Henry Walsh", "protocol", 6),
    ("Ruth Adekoya", "devrel", 3),
    ("Jin-ho Park", "bridge", 8),
    ("Mira Patel", "ops", 5),
    ("Otto Müller", "protocol", 7),
    ("Yuki Tanaka", "research", 11),
    ("Daria Volkova", "infra", 5),
    ("Ravi Subramanian", "bridge", 6),
    ("Elena Costa", "protocol", 4),
    ("Karim Hassan", "research", 9),
    ("Layla Ahmadi", "protocol", 6),
]


def hiring_events(facts: dict, start: str, end: str) -> Iterator[dict]:
    """Candidate intros in #hiring-loops, interview Calendar events, Notion
    decision docs. Captures: time-to-close per role, panel diversity,
    decline patterns, leveling distribution."""
    d0 = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    d1 = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)

    yield {"t": _iso(_midday(d0.date(), hour=10)),
           "provider": "slack", "kind": "channel.create",
           "payload": {"id": HIRING_CHANNEL, "name": "hiring-loops",
                       "is_private": True}}

    recruiter = "person:delbonis"  # cofounder runs early hiring
    leads_by_team = {}
    for team_id, members in _PEOPLE_BY_TEAM.items():
        if not members:
            continue
        leads_by_team[team_id] = max(
            members, key=lambda pid: next(
                (p["commits"] for p in facts["people"] if p["id"] == pid), 0),
        )

    days_span = max(1, (d1 - d0).days)
    n_candidates = min(len(CANDIDATE_POOL),
                       max(8, days_span // 90))   # ~one per quarter min
    for i, (name, team_short, loop_days) in enumerate(CANDIDATE_POOL[:n_candidates]):
        team_id = f"team:{team_short}"
        team_lead = leads_by_team.get(team_id)
        candidate_id = f"candidate:{i}-{name.split()[0].lower()}"
        # Stagger candidate intros across the corpus span.
        intro_day = d0 + timedelta(days=int((i + 1) * days_span / (n_candidates + 1)))
        if intro_day > d1:
            break
        # Slack intro thread.
        intro_text = (f"new candidate in pipeline — {name}, {team_short} team, "
                      f"sourced via referral, panel kicks off this week")
        yield {"t": _iso(_skew_to_peak(intro_day, recruiter)),
               "provider": "slack", "kind": "message",
               "actor": recruiter,
               "payload": {"channel": HIRING_CHANNEL, "text": intro_text,
                           "category": "hiring:intro",
                           "candidate": candidate_id}}
        # Calendar interview loop (4-6 sessions over loop_days).
        n_sessions = 4 + _hash_int(candidate_id, "n_sess", n=3)
        panel = [pid for pid in (team_lead, recruiter,
                                 leads_by_team.get("team:research"),
                                 leads_by_team.get("team:infra")) if pid]
        if not panel:
            panel = [recruiter]
        for s in range(n_sessions):
            session_day = intro_day + timedelta(days=1 + s * max(1, loop_days // n_sessions))
            if session_day > d1:
                break
            interviewer = panel[s % len(panel)]
            slot_h = 14 + (s % 4)
            when = session_day.replace(hour=slot_h, minute=0)
            yield {"t": _iso(when), "provider": "calendar",
                   "kind": "event.create",
                   "actor": recruiter,
                   "payload": {"id": f"cal:interview:{candidate_id}:{s}",
                               "summary": f"Interview — {name} ({['phone','tech','system','culture','team','offer'][s % 6]})",
                               "start": _iso(when),
                               "end": _iso(when + timedelta(minutes=45)),
                               "attendees": [recruiter, interviewer],
                               "category": "interview",
                               "candidate": candidate_id}}
        # Notion decision doc.
        decision_day = intro_day + timedelta(days=loop_days + 2)
        if decision_day > d1:
            continue
        # ~65% hire rate (matches typical post-loop conversion).
        hired = _hash_int(candidate_id, "decision", n=100) < 65
        decision = "Hire" if hired else "Pass"
        yield {"t": _iso(_skew_to_peak(decision_day, recruiter)),
               "provider": "notion", "kind": "page.create",
               "actor": recruiter,
               "payload": {"id": f"notion:hire:{candidate_id}",
                           "title": f"Hiring decision — {name}",
                           "kind": "hiring_decision",
                           "body_md": (
                               f"# Hiring decision — {name}\n"
                               f"Team: {team_short}\n"
                               f"Loop duration: {loop_days} days\n\n"
                               f"## Decision: {decision}\n\n"
                               f"## Panel feedback\n"
                               + "\n".join(f"- {_resolve_handle(p, facts)}: positive on {team_short} fundamentals" for p in panel[:3])
                               + "\n"
                           ),
                           "is_private": True,
                           "audience": panel,
                           "decision": decision,
                           "candidate": candidate_id}}
        # Follow-up Slack: outcome announce.
        announce = (f"offer extended to {name} — {team_short} team" if hired
                    else f"closing the loop on {name} — passing, will keep in pipeline")
        yield {"t": _iso(_skew_to_peak(decision_day + timedelta(hours=4), recruiter)),
               "provider": "slack", "kind": "message",
               "actor": recruiter,
               "payload": {"channel": HIRING_CHANNEL, "text": announce,
                           "category": f"hiring:{decision.lower()}",
                           "candidate": candidate_id}}


# -----------------------------------------------------------------------------
# Gmail external comms (Tier 3)
# -----------------------------------------------------------------------------

INVESTOR_EMAILS = [
    ("investor:thiel-capital", "Eric Yu", "eric.yu@thielcapital.example"),
    ("investor:multicoin", "Tushar Jain", "tushar@multicoin.capital.example"),
    ("investor:variant", "Spencer Noon", "spencer@variant.fund.example"),
]
AUDIT_FIRMS = [
    ("audit:halborn", "Steven Walbroehl", "steven@halborn.com.example"),
    ("audit:trail-of-bits", "Dan Guido", "dan@trailofbits.com.example"),
]
PARTNERS = [
    ("partner:starknet", "Eli Ben-Sasson", "eli@starkware.co.example"),
    ("partner:btcpay", "Nicolas Dorier", "nicolas@btcpay.org.example"),
]


def gmail_events(facts: dict, start: str, end: str) -> Iterator[dict]:
    """External comms: investor quarterly updates, audit firm correspondence,
    partner discussions. Pure gmail.message events.

    Detectable signals:
      - quarterly investor cadence with delbonis as primary author
      - audit firm volume spikes around audit windows
      - partner threads recur as collaboration deepens
    """
    d0 = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    d1 = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)

    cofounder = "person:delbonis"

    # Quarterly investor updates.
    cur = d0
    while cur < d1:
        if cur.day == 1 and cur.month in (1, 4, 7, 10):
            for inv_id, name, email in INVESTOR_EMAILS:
                when = _skew_to_peak(cur, cofounder)
                yield {"t": _iso(when), "provider": "gmail", "kind": "message",
                       "actor": cofounder,
                       "payload": {
                           "from": "delbonis@alpenlabs.io",
                           "to": [email],
                           "subject": f"Alpen Labs — Q{((cur.month - 1) // 3) + 1} {cur.year} update",
                           "body": (
                               f"Hi {name.split()[0]},\n\n"
                               f"Here's our quarterly progress update. Highlights:\n"
                               f"- Strata testnet remains stable; bridge throughput +18% qoq\n"
                               f"- Glock verifier hit our internal latency target\n"
                               f"- Headcount: 36 (+3 this quarter)\n"
                               f"- Runway: ~22 months at current burn\n\n"
                               f"Happy to jump on a call if useful.\n\n"
                               f"Best,\nTrey"
                           ),
                           "category": "investor_update",
                           "thread": inv_id}}
                # Reply 1-3 days later
                reply_when = when + timedelta(days=1 + _hash_int(inv_id, str(cur.toordinal()), n=2))
                yield {"t": _iso(reply_when), "provider": "gmail", "kind": "message",
                       "actor": None,
                       "payload": {
                           "from": email,
                           "to": ["delbonis@alpenlabs.io"],
                           "subject": f"Re: Alpen Labs — Q{((cur.month - 1) // 3) + 1} {cur.year} update",
                           "body": f"Thanks Trey, great progress. One question on bridge throughput — what's the new bottleneck? — {name}",
                           "category": "investor_reply",
                           "thread": inv_id}}
        cur += timedelta(days=1)

    # Audit firm correspondence: clustered around the 3 external audit events
    # from office_life.yaml.
    for ev in _OFFICE.get("external_events", []):
        if ev["kind"] != "disclosure" and "audit" not in (ev.get("label") or "").lower():
            continue
        try:
            evd = datetime.fromisoformat(ev["date"]).replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if evd < d0 or evd > d1:
            continue
        firm_id, contact, email = AUDIT_FIRMS[_hash_int(ev["label"], "firm",
                                                        n=len(AUDIT_FIRMS))]
        # Kickoff email
        yield {"t": _iso(evd), "provider": "gmail", "kind": "message",
               "actor": cofounder,
               "payload": {
                   "from": "delbonis@alpenlabs.io",
                   "to": [email], "subject": f"Engagement kickoff — {ev['label']}",
                   "body": f"Hi {contact.split()[0]}, ready to kick off the engagement. Will share scope doc today.",
                   "category": "audit",
                   "thread": firm_id}}
        # 4-6 follow-up exchanges over 2 weeks
        for k in range(4 + _hash_int(ev["label"], "k", n=3)):
            who = cofounder if k % 2 == 0 else None
            when = evd + timedelta(days=k * 2 + 1, hours=10 + k)
            if when > d1:
                break
            yield {"t": _iso(when), "provider": "gmail", "kind": "message",
                   "actor": who,
                   "payload": {
                       "from": ("delbonis@alpenlabs.io" if who == cofounder
                                else email),
                       "to": ([email] if who == cofounder
                              else ["delbonis@alpenlabs.io"]),
                       "subject": f"Re: Engagement — {ev['label']}",
                       "body": f"Following up on the open question about scope item {k+1}.",
                       "category": "audit",
                       "thread": firm_id}}

    # Partner discussions: 1-2 threads per partner, sparse.
    for pi, (p_id, pname, p_email) in enumerate(PARTNERS):
        n_threads = 1 + _hash_int(p_id, "n_thr", n=2)
        for ti in range(n_threads):
            start_day = d0 + timedelta(days=(pi * 4 + ti * 8 + 60) * 30 + _hash_int(p_id, str(ti), n=60))
            if start_day > d1:
                continue
            yield {"t": _iso(_skew_to_peak(start_day, cofounder)),
                   "provider": "gmail", "kind": "message",
                   "actor": cofounder,
                   "payload": {
                       "from": "delbonis@alpenlabs.io",
                       "to": [p_email],
                       "subject": f"Collaboration check-in — {pname}",
                       "body": f"Hi {pname.split()[0]}, sharing some thoughts on a Bitcoin-side primitive we've been discussing.",
                       "category": "partner",
                       "thread": p_id}}


# -----------------------------------------------------------------------------
# Notion edit history (page.update events)
# -----------------------------------------------------------------------------

def notion_edit_events(facts: dict) -> Iterator[dict]:
    """Replay each high-signal Notion page as 1-4 edit events showing
    ownership migration. Detected as 'X started this RFC but Y took over' or
    'this doc has been edited 12 times in 3 months — high contention'."""
    for tfp in sorted(THREADS.glob("THR-*.yaml")):
        thread = yaml.safe_load(tfp.read_text())
        for art in thread.get("artifacts") or []:
            bid = art.get("beat") or "B1"
            # Find the originating beat for timing.
            beat = next((b for b in thread.get("beats") or []
                         if b.get("id") == bid), None)
            if not beat:
                continue
            try:
                anchor = datetime.combine(beat["start"], datetime.min.time(),
                                          tzinfo=timezone.utc) + timedelta(hours=4)
            except Exception:
                continue
            page_id = f"notion:{thread['id']}:{bid}"
            # 1-4 edits over the beat window. Authors rotate through cast.
            cast = thread.get("cast") or []
            n_edits = 1 + _hash_int(page_id, "n_e", n=4)
            for ei in range(n_edits):
                editor = cast[(ei + 1) % len(cast)] if cast else art.get("author")
                edit_when = anchor + timedelta(days=2 + ei * 4)
                yield {"t": _iso(edit_when),
                       "provider": "notion", "kind": "page.update",
                       "actor": editor,
                       "payload": {"id": page_id,
                                   "summary": ["clarified scope", "added test plan",
                                               "addressed review feedback",
                                               "tightened wording"][ei % 4]}}


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-mirror", action="store_true",
                    help="include the full GitHub real-mirror (large)")
    ap.add_argument("--include-rituals", action="store_true",
                    help="include weekly standups + monthly all-hands")
    ap.add_argument("--include-chatter", action="store_true",
                    help="include #random chatter, PTO announces, news reactions")
    ap.add_argument("--include-1on1s", action="store_true",
                    help="include weekly 1:1 calendar events + private Notion notes")
    ap.add_argument("--include-ci-flakes", action="store_true",
                    help="include CI flake bursts + production incident postmortems")
    ap.add_argument("--include-hiring", action="store_true",
                    help="include hiring pipeline events (candidates, interviews, decisions)")
    ap.add_argument("--include-gmail", action="store_true",
                    help="include Gmail external comms (investor / audit / partner)")
    ap.add_argument("--include-notion-edits", action="store_true",
                    help="include Notion page.update history (edit migration)")
    ap.add_argument("--include-all", action="store_true",
                    help="shortcut: turn on every --include-*")
    ap.add_argument("--end", default="2026-05-29",
                    help="ritual cutoff date (default: today)")
    args = ap.parse_args()

    facts = yaml.safe_load(FACTS.read_text())
    thread_files = sorted(THREADS.glob("THR-*.yaml"))
    global _VOICES, _PATTERNS, _CHATTER, _OFFICE
    _VOICES = _load_voices()
    _PATTERNS = _load_patterns()
    _CHATTER = _load_chatter()
    _OFFICE = _load_office_life()
    _build_manager_map(facts)
    pto_n = sum(len(v) for v in (_OFFICE.get("pto") or {}).values())
    print(f"facts: {len(facts['people'])} people, {len(facts['repos'])} repos, "
          f"{len(thread_files)} threads, {len(_VOICES)} voices, "
          f"{len(_PATTERNS)} patterns, {len(_CHATTER)} chatter banks, "
          f"{pto_n} pto windows, {len(_MANAGER_OF)} mgr pairs",
          file=sys.stderr)

    events: list[dict] = []
    events.extend(bootstrap_events(facts))
    for tfp in thread_files:
        thread = yaml.safe_load(tfp.read_text())
        events.extend(thread_events(thread, facts))
    if args.include_mirror or args.include_all:
        events.extend(github_mirror_events(facts))
    if args.include_rituals or args.include_all:
        events.extend(ritual_events(facts, facts["company"]["founded"], args.end))
    if args.include_chatter or args.include_all:
        events.extend(chatter_events(facts, facts["company"]["founded"], args.end))
    if args.include_1on1s or args.include_all:
        events.extend(one_on_one_events(facts, facts["company"]["founded"], args.end))
    if args.include_ci_flakes or args.include_all:
        events.extend(ci_flake_events(facts, facts["company"]["founded"], args.end))
    if args.include_hiring or args.include_all:
        events.extend(hiring_events(facts, facts["company"]["founded"], args.end))
    if args.include_gmail or args.include_all:
        events.extend(gmail_events(facts, facts["company"]["founded"], args.end))
    if args.include_notion_edits or args.include_all:
        events.extend(notion_edit_events(facts))

    # Stable sort by timestamp.
    events.sort(key=lambda e: e["t"])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    # Summarize.
    by_provider: dict[str, int] = {}
    for ev in events:
        by_provider[ev["provider"]] = by_provider.get(ev["provider"], 0) + 1
    print(f"wrote {OUT}  total={len(events)}", file=sys.stderr)
    for k, v in sorted(by_provider.items(), key=lambda x: -x[1]):
        print(f"  {k:10s} {v}", file=sys.stderr)


if __name__ == "__main__":
    main()
