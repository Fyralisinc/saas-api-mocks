"""Static parse of the Gharelu-Alpen corpus → a rich dossier the UI renders.

Everything here is deterministic and corpus-derived: company facts (mission,
products, repos, milestones), people enriched with behavioral patterns, and a
per-month signal-count + active-thread roll-up across the full ~58-month
corpus. We cache the result by (path, mtime, size) so the parse runs once and
every /api/state call after that is a dict lookup.

The dossier returned by this module has no dynamic state — `narrative.build`
merges it with the run's virtual_now to produce the final shape the UI gets.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


# Per-provider, how many ingestion rows a single corpus event produces.
# Used to translate the unique-signal count into "what Fyralis would see if it
# pulled every provider table". Most providers are 1:1; gmail doubles because
# every send lands in the sender's Sent mailbox AND the recipient's Inbox.
INGEST_MULTIPLIER: dict[str, dict[str, float]] = {
    "slack":    {"message": 1.0},
    "discord":  {"message": 1.0},
    "github":   {"commit": 1.0, "pr.open": 1.0, "pr.merge": 1.0, "pr.close": 1.0,
                 "issue.create": 1.0, "issue.open": 1.0, "issue.close": 1.0,
                 "issue.assign": 1.0, "review.submit": 1.0, "release.publish": 1.0},
    "gmail":    {"message": 2.0},   # sender mailbox + recipient mailbox
    "calendar": {"event.create": 1.0},
    "notion":   {"page.create": 1.0, "page.update": 1.0},
    "drive":    {"file.create": 1.0},
    "jira":     {"issue.create": 1.0, "issue.transition": 1.0, "issue.assign": 1.0},
}

SIGNAL_NOTES = {
    "slack":    "Each row = one message in channel history. Threaded replies count separately.",
    "discord":  "Each row = one message in a guild channel. Threaded replies count separately.",
    "github":   "Commits, PR open/merge/close, issues, reviews and releases each count as one signal.",
    "gmail":    "1 send. Ingestion sees 2 rows: sender's Sent mailbox + recipient's Inbox.",
    "calendar": "1 event create. The same event is visible on every attendee's calendar.",
    "notion":   "page.create and page.update each = one signal. Edits to the same page count separately.",
    "drive":    "1 file create per signal. Revisions on existing files count separately.",
    "jira":     "issue.create, issue.transition, issue.assign each = one signal.",
}

# Phase grouping — months → narrative "phase" the company was in.
# Boundaries derived from curated milestones in facts.yaml.
PHASE_BOUNDS = [
    ("2022-01", "2024-03", "Pre-product foundations",
     "Whitepaper, founding-team formation, no public product yet."),
    ("2024-04", "2024-12", "Public launch & technical thesis",
     "First blog post, repos open-sourced, early Strata protocol work."),
    ("2025-01", "2025-07", "Strategic round & Strata maturation",
     "Funding closed, Bridge + Bitcoin-BOSD work, Glock verification research."),
    ("2025-08", "2025-11", "Testnet + Glock public release",
     "Public testnet live, Glock published, Starknet partnership."),
    ("2025-12", "2026-04", "Mosaic build-out & Prague support",
     "Mosaic garbled-circuit protocol, Prague testnet, bridge state machine docs."),
    ("2026-05", "2030-12", "Mosaic public + Bitcoin Dollar",
     "Mosaic announcement, Bitcoin Dollar / credit-markets prep."),
]


_CACHE: dict[str, Any] = {}


def load_profile(corpus_path: str | os.PathLike) -> dict:
    """Return the cached static profile. Re-parses if events.jsonl changes."""
    p = Path(corpus_path)
    stat = p.stat() if p.exists() else None
    key = (str(p), getattr(stat, "st_mtime", 0), getattr(stat, "st_size", 0))
    if _CACHE.get("key") == key:
        return _CACHE["profile"]
    profile = _build_profile(p)
    _CACHE["key"] = key
    _CACHE["profile"] = profile
    return profile


def _build_profile(events_jsonl: Path) -> dict:
    corpus_root = events_jsonl.parent.parent       # corpus/build/events.jsonl -> corpus/
    facts = _yaml(corpus_root / "facts" / "facts.yaml")
    enriched = _yaml(corpus_root / "facts" / "people.enriched.yaml")
    patterns_raw = _yaml(corpus_root / "facts" / "patterns.yaml")
    patterns = (patterns_raw or {}).get("patterns", {})
    threads = _load_threads(corpus_root / "threads")

    company = (facts or {}).get("company", {})
    products = (facts or {}).get("products", [])
    repos = (facts or {}).get("repos", [])
    milestones = (facts or {}).get("milestones_curated", []) or []
    people_facts = (facts or {}).get("people", []) or []
    people_enriched = (enriched or {}).get("people", []) or []

    enriched_by_id = {p["id"]: p for p in people_enriched}
    facts_by_id = {p["id"]: p for p in people_facts}

    # ---- people, with behavior summary ----
    people = []
    for pid in sorted(set(list(enriched_by_id) + list(facts_by_id)),
                      key=lambda i: -(_safe_int(facts_by_id.get(i, {}).get("commits")) or
                                       _safe_int(enriched_by_id.get(i, {}).get("commits")) or 0)):
        f = facts_by_id.get(pid, {})
        e = enriched_by_id.get(pid, {})
        pat = patterns.get(pid, {}) or {}
        people.append(_person(pid, f, e, pat))

    # ---- monthly signal roll-up from events.jsonl ----
    # signals[ym][provider] = unique count (corpus events)
    # ingest[ym][provider] = "rows Fyralis would see" (using INGEST_MULTIPLIER)
    signals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    ingest:  dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    repo_activity: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    actor_activity: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    first_ts: str | None = None
    last_ts: str | None = None
    if events_jsonl.exists():
        with events_jsonl.open() as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = e.get("t") or ""
                if len(t) < 7:
                    continue
                first_ts = first_ts or t
                last_ts = t
                ym = t[:7]
                provider = e.get("provider") or "?"
                kind = e.get("kind") or ""
                if provider == "org":
                    continue                     # team/person/repo provisioning noise
                signals[ym][provider] += 1
                mult = INGEST_MULTIPLIER.get(provider, {}).get(kind, 1.0)
                ingest[ym][provider] += mult
                # Top-of-mind aggregates so we can narrate the month.
                payload = e.get("payload") or {}
                if provider == "github" and kind == "commit":
                    repo = payload.get("repo")
                    if repo:
                        repo_activity[ym][repo] += 1
                actor = payload.get("actor") or payload.get("author") or e.get("actor")
                if isinstance(actor, str) and actor.startswith("person:"):
                    actor_activity[ym][actor] += 1

    # totals across the whole corpus
    totals_corpus: dict[str, int] = defaultdict(int)
    ingest_corpus: dict[str, int] = defaultdict(int)
    for ym, by_prov in signals.items():
        for p, n in by_prov.items():
            totals_corpus[p] += n
    for ym, by_prov in ingest.items():
        for p, n in by_prov.items():
            ingest_corpus[p] += int(round(n))

    # ---- month list — span the full corpus window so the UI can render
    #      "future / not yet replayed" months consistently.
    months = _month_range(first_ts, last_ts) if first_ts and last_ts else []
    monthly = []
    for ym in months:
        phase = _phase_for(ym)
        thread_beats = _active_beats(threads, ym)
        new_hires = [p for p in people if _ym(p.get("started_at")) == ym]
        ms = [m for m in milestones if _ym(m.get("date")) == ym]
        sig = {k: int(v) for k, v in signals.get(ym, {}).items()}
        ing = {k: int(round(v)) for k, v in ingest.get(ym, {}).items()}
        top_repos = sorted(repo_activity.get(ym, {}).items(),
                           key=lambda kv: -kv[1])[:3]
        top_actors = sorted(actor_activity.get(ym, {}).items(),
                            key=lambda kv: -kv[1])[:3]
        monthly.append({
            "ym": ym,
            "label": _month_label(ym),
            "phase": phase["title"],
            "phase_blurb": phase["blurb"],
            "signals": sig,
            "ingest_signals": ing,
            "unique_total": sum(sig.values()),
            "ingest_total": sum(ing.values()),
            "milestones": [{"date": m.get("date"), "title": m.get("title"),
                            "kind": m.get("kind")} for m in ms],
            "threads_active": [
                {"id": b["thread_id"], "title": b["thread_title"],
                 "beat_kind": b["beat_kind"], "summary": b["beat_summary"]}
                for b in thread_beats[:4]
            ],
            "new_hires": [{"handle": p["handle"], "full_name": p["full_name"],
                           "role": p["role"], "team": p["team"]} for p in new_hires],
            "top_repos": [{"repo": r, "commits": n} for r, n in top_repos],
            "top_actors": [
                {"handle": _handle_for_pid(people, pid), "commits": n}
                for pid, n in top_actors
            ],
            "narrative": _month_narrative(
                ym, sig, ing, ms, thread_beats, new_hires, top_repos, top_actors, people,
            ),
        })

    # ---- top-level overview text ----
    repos_total = len(repos)
    repos_top = sorted(repos, key=lambda r: -_safe_int(r.get("stars")) or 0)[:8]
    teams = _teams(people)
    overview_blurb = _overview_blurb(company, products, len(people), repos_total)

    return {
        "company": {
            "name": company.get("name", "Alpen Labs"),
            "display_name": "Gharelu-Alpen",
            "mission": company.get("mission", ""),
            "founded": company.get("founded", "2022-01-01"),
            "stage": "Series-A protocol startup",
            "homepage": company.get("homepage"),
            "blog": company.get("blog"),
            "github_org": company.get("github_org"),
            "headcount": len(people),
            "overview_blurb": overview_blurb,
        },
        "products": [{"name": p.get("name"), "description": p.get("description")}
                     for p in products],
        "repos": {
            "total": repos_total,
            "top": [{"name": r.get("name"), "stars": _safe_int(r.get("stars")) or 0,
                     "language": r.get("language") or "",
                     "description": (r.get("description") or "").strip(),
                     "created_at": r.get("created_at"),
                     "archived": bool(r.get("archived"))}
                    for r in repos_top],
        },
        "milestones": [
            {"date": m.get("date"), "kind": m.get("kind"), "title": m.get("title")}
            for m in milestones
        ],
        "teams": teams,
        "people": people,
        "signal_notes": SIGNAL_NOTES,
        "monthly": {
            "months": monthly,
            "totals_corpus": dict(totals_corpus),
            "ingest_corpus": dict(ingest_corpus),
            "first_ts": first_ts,
            "last_ts": last_ts,
            "month_count": len(months),
            "phase_legend": [
                {"start": s, "end": e, "title": t, "blurb": b}
                for (s, e, t, b) in PHASE_BOUNDS
            ],
        },
    }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _yaml(p: Path) -> dict | None:
    if not p.exists():
        return None
    with p.open() as fh:
        return yaml.safe_load(fh)


def _load_threads(d: Path) -> list[dict]:
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("*.yaml")):
        with f.open() as fh:
            try:
                y = yaml.safe_load(fh) or {}
                out.append(y)
            except yaml.YAMLError:
                continue
    return out


def _ym(v: Any) -> str:
    """Coerce a date / datetime / YYYY-MM-DD string to 'YYYY-MM'. Empty on failure."""
    if v is None:
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m")
    s = str(v)
    return s[:7] if len(s) >= 7 else ""


def _trim(s: str, n: int) -> str:
    """Trim at a word boundary so a summary doesn't end mid-syllable."""
    if not s or len(s) <= n:
        return s
    cut = s.rfind(" ", 0, n)
    return (s[:cut] if cut > 0 else s[:n]).rstrip(",;:.") + "…"


def _safe_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _month_range(start_ts: str, end_ts: str) -> list[str]:
    sy, sm = int(start_ts[:4]), int(start_ts[5:7])
    ey, em = int(end_ts[:4]), int(end_ts[5:7])
    out = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y += 1; m = 1
    return out


def _month_label(ym: str) -> str:
    dt = datetime.strptime(ym, "%Y-%m")
    return dt.strftime("%B %Y")


def _phase_for(ym: str) -> dict:
    for (s, e, t, b) in PHASE_BOUNDS:
        if s <= ym <= e:
            return {"title": t, "blurb": b}
    return {"title": "Future", "blurb": "Beyond the current corpus window."}


def _active_beats(threads: list[dict], ym: str) -> list[dict]:
    """For a given month, return all beats whose window overlaps that month.

    A beat overlaps month YM iff beat.start[:7] <= YM <= beat.end[:7].
    """
    out = []
    for th in threads:
        tid = th.get("id") or ""
        ttitle = th.get("title") or tid
        beats = th.get("beats") or []
        for b in beats:
            s = _ym(b.get("start"))
            e = _ym(b.get("end"))
            if not s or not e:
                continue
            if s <= ym <= e:
                out.append({
                    "thread_id": tid,
                    "thread_title": ttitle,
                    "beat_id": b.get("id"),
                    "beat_kind": b.get("kind"),
                    "beat_summary": (b.get("summary") or "").strip(),
                })
    return out


def _handle_for_pid(people: list[dict], pid: str) -> str:
    for p in people:
        if p["id"] == pid:
            return p["handle"]
    return pid.removeprefix("person:")


def _format_hours(peak: float, spread: float) -> str:
    lo = int(round(peak - spread)) % 24
    hi = int(round(peak + spread)) % 24
    fmt = lambda h: ("12am" if h == 0 else "12pm" if h == 12
                     else f"{h}am" if h < 12 else f"{h-12}pm")
    return f"{fmt(lo)}–{fmt(hi)} local"


def _person(pid: str, f: dict, e: dict, pat: dict) -> dict:
    handle = f.get("github_handle") or e.get("github_handle") or pid.removeprefix("person:")
    full_name = e.get("full_name") or f.get("full_name") or handle
    if full_name == "needs:review":
        full_name = handle
    role = f.get("role") or e.get("role") or "?"
    level = f.get("level") or e.get("level") or "ic"
    team = (f.get("team") or e.get("team") or "team:?")
    started = f.get("started_at") or e.get("started_at")
    commits = _safe_int(f.get("commits")) or _safe_int(e.get("commits")) or 0
    bio = (e.get("bio") or f.get("bio") or "").strip()
    location = (e.get("location") or "").strip()
    top_repos = list((f.get("top_repos") or e.get("top_repos") or []))

    peak = float(pat.get("msg_hour_peak", 14))
    spread = float(pat.get("msg_hour_spread", 2.5))
    weekend_factor = float(pat.get("weekend_msg_factor", 0.3))
    standup = float(pat.get("standup_attendance", 0.8))
    ship_lag = float(pat.get("ship_lag_hours", 0.0))
    review_resp = float(pat.get("review_response_hours", 12.0))
    review_thor = float(pat.get("review_thoroughness", 0.8))

    # Every phrase below has to anchor in something Fyralis can actually
    # observe across the mock APIs, so the model layer can be scored
    # against it. Anchors are noted in the comments.

    # weekend_factor: ratio of weekend message volume to weekday baseline.
    # Anchor: density of Slack + Discord message timestamps on Sat/Sun vs
    # Mon–Fri across this person's authored history.
    wknd_pct = int(round(weekend_factor * 100))
    weekend_blurb = (
        f"works weekends regularly (~{wknd_pct}% of weekday volume)" if weekend_factor > 0.7
        else f"some weekend activity (~{wknd_pct}% of weekday volume)" if weekend_factor > 0.3
        else f"rarely works weekends (~{wknd_pct}% of weekday volume)"
    )

    # ship_lag: hours late vs the Jira issue's planned end date.
    # Anchor: app_jira.issues.resolution_date − the Jira issue's planned
    # end (renderer sets this when the ticket is created).
    ship_blurb = (
        f"chronic slipper — closes Jira tickets ~{int(ship_lag)}h after their planned end date" if ship_lag > 24
        else f"mild slip — closes Jira tickets ~{int(ship_lag)}h after their planned end date" if ship_lag > 6
        else f"closes Jira tickets ~{abs(int(ship_lag))}h ahead of the planned end date" if ship_lag < -6
        else "closes Jira tickets on the planned end date"
    )

    # review_thoroughness: probability a review is NOT a rubber-stamp.
    # Anchor: length + structure of app_github.reviews.body and presence
    # of inline comments on the PR.
    review_blurb = (
        f"leaves detailed PR review comments ({int(review_thor*100)}% non-rubber-stamp)" if review_thor >= 0.85
        else f"writes moderate PR review comments ({int(review_thor*100)}% non-rubber-stamp)" if review_thor >= 0.7
        else f"often rubber-stamps PR reviews ({int(review_thor*100)}% non-rubber-stamp)"
    )

    # standup_attendance: per-event probability of accepting the recurring
    # standup invite. Anchor: app_calendar.events attendee.response_status
    # on the team's recurring standup series.
    attend_blurb = f"Attends {int(standup*100)}% of standup calendar invites"

    # review_response_hours: median delay from being requested as reviewer
    # to submitting the review. Anchor: app_github.reviews.submitted_at vs
    # the corresponding review_request event on the PR.
    resp_blurb = f"responds to PR review requests within ~{review_resp:.0f}h"

    # "ic" / "senior" / "staff" / "principal" — render IC in caps; everything
    # else gets normal title-case.
    level_disp = "IC" if level.lower() == "ic" else level.capitalize()
    role_disp = "developer relations" if role == "devrel" else role
    behavior = (
        f"{level_disp} {role_disp}"
        + (f" on {team.removeprefix('team:').capitalize()}" if team.startswith("team:") else "")
        + (f", based in {location}" if location else "")
        + f". Most active {_format_hours(peak, spread)}; {weekend_blurb}. "
        + f"{attend_blurb}, {resp_blurb}, {review_blurb}; "
        + f"{ship_blurb}."
    )

    return {
        "id": pid,
        "handle": handle,
        "full_name": full_name,
        "role": role,
        "level": level,
        "team": team,
        "started_at": started,
        "location": location,
        "bio": bio,
        "commits": commits,
        "top_repos": top_repos,
        "patterns": {
            "peak_hour": peak,
            "hour_spread": spread,
            "active_window": _format_hours(peak, spread),
            "weekend_factor": weekend_factor,
            "standup_attendance_pct": int(round(standup * 100)),
            "ship_lag_hours": ship_lag,
            "review_response_hours": review_resp,
            "review_thoroughness_pct": int(round(review_thor * 100)),
        },
        "behavior_summary": behavior,
    }


def _teams(people: list[dict]) -> list[dict]:
    by_team: dict[str, list[dict]] = defaultdict(list)
    for p in people:
        by_team[p["team"]].append(p)
    out = []
    for team, members in sorted(by_team.items(), key=lambda kv: -len(kv[1])):
        # most senior + most commits = de facto lead
        lead = sorted(members, key=lambda p: (-1 if p["level"] == "senior" else 0,
                                              -p["commits"]))[0] if members else None
        out.append({
            "name": team.removeprefix("team:").capitalize() if team.startswith("team:") else team,
            "team_id": team,
            "headcount": len(members),
            "lead": lead["full_name"] if lead else None,
            "lead_handle": lead["handle"] if lead else None,
        })
    return out


def _overview_blurb(company: dict, products: list[dict], headcount: int, repos: int) -> str:
    product_names = [p.get("name") for p in products if p.get("name")]
    return (
        f"{company.get('name', 'Alpen Labs')} is a Bitcoin-native finance protocol startup "
        f"founded in {company.get('founded', '2022')[:4]}, building toward "
        f"\"{company.get('mission', '')}\". Their stack centres on a Bitcoin rollup "
        f"({product_names[0] if product_names else 'Strata'}), a SNARK-verification primitive "
        f"({product_names[1] if len(product_names) > 1 else 'Glock'}), a garbled-circuit "
        f"verifier {product_names[2] if len(product_names) > 2 else 'Mosaic'}, and "
        f"reference bridge / credit-market products on top. Engineering is "
        f"organised as Protocol, Bridge, Infrastructure, Research, DevRel and Ops teams; "
        f"{headcount} active contributors across {repos} public repos at the snapshot."
    )


def _month_narrative(
    ym: str,
    sig: dict[str, int], ing: dict[str, int],
    milestones: list[dict], beats: list[dict],
    new_hires: list[dict], top_repos: list[tuple[str, int]],
    top_actors: list[tuple[str, int]], people: list[dict],
) -> str:
    parts: list[str] = []

    if milestones:
        ms = ", ".join(m.get("title", "?") for m in milestones)
        parts.append(f"Milestone: {ms}.")

    if new_hires:
        nh = ", ".join(f"{p['full_name']} ({p['role']}, {p['team'].removeprefix('team:')})"
                       for p in new_hires[:4])
        parts.append(f"Hires: {nh}.")

    if beats:
        # one liner per active thread/beat
        seen_threads = set()
        beat_lines = []
        for b in beats[:3]:
            if b["thread_id"] in seen_threads:
                continue
            seen_threads.add(b["thread_id"])
            kind = b["beat_kind"] or "active"
            beat_lines.append(f"{b['thread_title']} — {kind}: {_trim(b['beat_summary'], 180)}")
        if beat_lines:
            parts.append(" · ".join(beat_lines))

    if top_repos:
        tr = ", ".join(f"{r} ({n})" for r, n in top_repos)
        parts.append(f"Code activity concentrated in: {tr}.")

    if top_actors:
        names = []
        for pid, n in top_actors:
            handle = _handle_for_pid(people, pid)
            names.append(f"{handle} ({n})")
        parts.append(f"Top contributors: {', '.join(names)}.")

    total = sum(sig.values())
    if total > 0:
        breakdown = ", ".join(f"{p}={n}"
                              for p, n in sorted(sig.items(), key=lambda kv: -kv[1])
                              if n > 0)
        parts.append(f"Signals: {total} unique ({breakdown}).")
    else:
        parts.append("No corpus activity this month.")

    return " ".join(parts)
