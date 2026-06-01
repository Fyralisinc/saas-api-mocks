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
import re
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
    "quickbooks": {"company.create": 1.0, "account.create": 1.0,
                   "vendor.create": 1.0, "employee.create": 1.0,
                   "deposit": 1.0, "purchase": 1.0},
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
    "quickbooks": "Deposits (cash in: funding rounds, grants) + purchases (cash out: payroll, opex, conferences, audits). Accounts, vendors, and employees seed once.",
}

# Phase grouping — months → narrative "phase" the company was in.
# Boundaries derived from curated milestones in facts.yaml.
PHASE_BOUNDS = [
    ("2024-02", "2024-04", "Pre-product foundations",
     "Cofounders alone — whitepaper, fundraising prep, first hires not in yet."),
    ("2024-05", "2024-12", "Public launch & team formation",
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
    office = _yaml(corpus_root / "facts" / "office_life.yaml") or {}
    pto_by_pid = (office.get("pto") or {})
    external_events = (office.get("external_events") or [])
    conference_travel = (office.get("conference_travel") or {})

    # Known round ids drive the thread → round mapping below. Read once so
    # the events.jsonl walk can match every thread key to the longest known
    # round id prefix (the fundraising emitter uses keys like
    # `round:seed-pitch-investor:ribbit`, which has to fold back into
    # `round:seed` not get split into 11 sub-rounds).
    finance = _yaml(corpus_root / "facts" / "finance.yaml") or {}
    known_round_ids = sorted(
        ((r.get("id") or "") for r in (finance.get("funding_rounds") or [])),
        key=lambda s: -len(s),     # longest first so prefix-match picks the most specific
    )

    company = (facts or {}).get("company", {})
    products = (facts or {}).get("products", [])
    repos = (facts or {}).get("repos", [])
    milestones = (facts or {}).get("milestones_curated", []) or []
    people_facts = (facts or {}).get("people", []) or []
    people_enriched = (enriched or {}).get("people", []) or []

    enriched_by_id = {p["id"]: p for p in people_enriched}
    facts_by_id = {p["id"]: p for p in people_facts}

    # ---- people, with behavior summary + status (active / departed / winding down) ----
    # invert conference_travel into per-person trips for richer per-person view
    travel_by_pid: dict[str, list[dict]] = defaultdict(list)
    for ev in external_events:
        if ev.get("kind") != "conference":
            continue
        attendees = conference_travel.get(ev["label"], []) or []
        for pid in attendees:
            travel_by_pid[pid].append({"label": ev["label"], "date": ev["date"], "days": ev.get("days", 4)})

    people = []
    for pid in sorted(set(list(enriched_by_id) + list(facts_by_id)),
                      key=lambda i: -(_safe_int(facts_by_id.get(i, {}).get("commits")) or
                                       _safe_int(enriched_by_id.get(i, {}).get("commits")) or 0)):
        f = facts_by_id.get(pid, {})
        e = enriched_by_id.get(pid, {})
        pat = patterns.get(pid, {}) or {}
        pto = pto_by_pid.get(pid, []) or []
        travel = travel_by_pid.get(pid, [])
        people.append(_person(pid, f, e, pat, pto, travel))

    # ---- monthly signal roll-up from events.jsonl ----
    # signals[ym][provider] = unique count (corpus events)
    # ingest[ym][provider] = "rows Fyralis would see" (using INGEST_MULTIPLIER)
    signals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    ingest:  dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    repo_activity: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    actor_activity: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    # Per-month finance: cash in (deposits), cash out (purchases), broken down
    # by category. Running bank balance is computed after the walk.
    cash_in:  dict[str, int] = defaultdict(int)
    cash_out: dict[str, int] = defaultdict(int)
    cash_out_by_cat: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    # Fundraising thread index — group every gmail fundraise_* / grant_* event
    # by (round_id, thread_key) so Page 2 can render a thread browser.
    fundraising_threads: dict[str, dict[str, dict]] = defaultdict(dict)
    # round_id -> { messages: int, threads: int, participants: set(addr) }

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
                # ---- finance roll-up
                if provider == "quickbooks" and kind == "deposit":
                    cash_in[ym] += int(payload.get("amount_usd", 0))
                elif provider == "quickbooks" and kind == "purchase":
                    amt = int(payload.get("amount_usd", 0))
                    cash_out[ym] += amt
                    cat = payload.get("category", "other")
                    cash_out_by_cat[ym][cat] += amt
                # ---- fundraising threads
                if provider == "gmail" and kind == "message":
                    cat = payload.get("category", "") or ""
                    if cat.startswith("fundraise_") or cat.startswith("grant_"):
                        thread = payload.get("thread") or ""
                        round_id = _round_id_from_thread(thread, known_round_ids)
                        if round_id:
                            bucket = fundraising_threads.setdefault(round_id, {})
                            tb = bucket.setdefault(thread, {
                                "thread": thread, "round_id": round_id,
                                "category": cat, "subject": payload.get("subject", ""),
                                "messages": [], "first_ts": t,
                            })
                            tb["messages"].append({
                                "t": t, "from": payload.get("from", ""),
                                "to": payload.get("to") or [],
                                "subject": payload.get("subject", ""),
                                "snippet": (payload.get("body", "") or "")[:240],
                                "category": cat,
                                "actor": e.get("actor"),
                            })

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
    # Running bank balance walks forward through the month list so each
    # month carries the end-of-month position. Anchored at 0 — the corpus's
    # founders-capital tranche on 2024-02-15 is itself a deposit that lifts
    # the balance the first month.
    running_balance = 0
    monthly = []
    for ym in months:
        phase = _phase_for(ym)
        thread_beats = _active_beats(threads, ym)
        new_hires = [p for p in people if _ym(p.get("started_at")) == ym]
        departures = [p for p in people if _ym(p.get("ended_at")) == ym]
        ms = [m for m in milestones if _ym(m.get("date")) == ym]
        ext = [{"date": ev.get("date"), "kind": ev.get("kind"),
                "label": ev.get("label"), "impact": ev.get("impact"),
                "days": ev.get("days", 1)}
               for ev in external_events if _ym(ev.get("date")) == ym]
        pto_this_month = _pto_in_month(pto_by_pid, ym, people)
        sig = {k: int(v) for k, v in signals.get(ym, {}).items()}
        ing = {k: int(round(v)) for k, v in ingest.get(ym, {}).items()}
        top_repos = sorted(repo_activity.get(ym, {}).items(),
                           key=lambda kv: -kv[1])[:3]
        top_actors = sorted(actor_activity.get(ym, {}).items(),
                            key=lambda kv: -kv[1])[:3]
        # finance
        m_in  = int(cash_in.get(ym, 0))
        m_out = int(cash_out.get(ym, 0))
        running_balance += m_in - m_out
        cash = {
            "cash_in_usd":  m_in,
            "cash_out_usd": m_out,
            "net_usd":      m_in - m_out,
            "bank_end_usd": running_balance,
            "out_by_category": dict(cash_out_by_cat.get(ym, {})),
        }
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
            "external_events": ext,
            "threads_active": [
                {"id": b["thread_id"], "title": b["thread_title"],
                 "beat_kind": b["beat_kind"], "summary": b["beat_summary"]}
                for b in thread_beats
            ],
            "new_hires": [{"handle": p["handle"], "full_name": p["full_name"],
                           "role": p["role"], "team": p["team"]} for p in new_hires],
            "departures": [{"handle": p["handle"], "full_name": p["full_name"],
                            "role": p["role"], "team": p["team"],
                            "ended_at": p["ended_at"]} for p in departures],
            "pto_this_month": pto_this_month,
            "top_repos": [{"repo": r, "commits": n} for r, n in top_repos],
            "top_actors": [
                {"handle": _handle_for_pid(people, pid), "commits": n}
                for pid, n in top_actors
            ],
            "cash": cash,
            "narrative": _month_narrative(
                ym, sig, ing, ms, thread_beats, new_hires, top_repos, top_actors, people,
                external_events=ext, departures=departures,
                pto_count=len(pto_this_month), phase_title=phase["title"],
            ),
        })

    # ---- finance summary across the corpus ----
    finance_totals = {
        "cash_in_usd":   sum(cash_in.values()),
        "cash_out_usd":  sum(cash_out.values()),
        "net_usd":       sum(cash_in.values()) - sum(cash_out.values()),
        "bank_end_usd":  running_balance,
        "out_by_category": _agg_category(cash_out_by_cat),
    }

    # ---- fundraising thread bundle for Page 2 ----
    # Bundle as a list of rounds with their threads grouped + sorted by time.
    # Round metadata pulled from facts.yaml's company.cofounders-adjacent
    # `funding_rounds` if present (or finance.yaml — we just need title + amount
    # + date for the round header). corpus_profile is intentionally decoupled
    # from finance.yaml, so we derive a minimal round meta from the thread
    # content itself.
    fundraising = _build_fundraising_bundle(
        fundraising_threads, corpus_root / "facts" / "finance.yaml")

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
            "founded": company.get("founded", "2024-02-01"),
            "stage": "Series-A protocol startup",
            "homepage": company.get("homepage"),
            "blog": company.get("blog"),
            "github_org": company.get("github_org"),
            "headcount": len(people),
            "overview_blurb": overview_blurb,
            "cofounders": [
                {"id": c.get("id"), "name": c.get("name"),
                 "title": c.get("title"), "linkedin": c.get("linkedin")}
                for c in (company.get("cofounders") or [])
            ],
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
        "fundraising": fundraising,
        "finance_totals": finance_totals,
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


def _agg_category(cash_out_by_cat: dict[str, dict[str, int]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for ym, by_cat in cash_out_by_cat.items():
        for cat, n in by_cat.items():
            out[cat] = out.get(cat, 0) + n
    return out


def _build_fundraising_bundle(
    fundraising_threads: dict[str, dict[str, dict]],
    finance_yaml: Path,
) -> dict:
    """Take the per-round nested dict of threads and shape it into a list of
    rounds the UI can render. Each round carries its threads sorted by start
    time; each thread carries its messages sorted by timestamp."""
    rounds_meta: dict[str, dict] = {}
    if finance_yaml.exists():
        try:
            d = yaml.safe_load(finance_yaml.read_text()) or {}
            for r in d.get("funding_rounds", []):
                rounds_meta[r["id"]] = {
                    "id": r["id"],
                    "kind": r.get("kind", "round"),
                    "date": str(r.get("date", "")),
                    "amount_usd": int(r.get("amount_usd", 0)),
                    "lead": r.get("lead", ""),
                    "participants": r.get("participants", []),
                    "note": r.get("note", ""),
                }
        except yaml.YAMLError:
            pass

    rounds: list[dict] = []
    for round_id, threads_by_key in fundraising_threads.items():
        meta = rounds_meta.get(round_id, {
            "id": round_id, "kind": "round", "date": "",
            "amount_usd": 0, "lead": "", "participants": [], "note": "",
        })
        thr_list = []
        for thread_key, info in threads_by_key.items():
            msgs = sorted(info["messages"], key=lambda m: m["t"])
            thr_list.append({
                "thread": thread_key,
                "subject": info["subject"],
                "category": info["category"],
                "messages": msgs,
                "first_ts": msgs[0]["t"] if msgs else "",
                "msg_count": len(msgs),
            })
        thr_list.sort(key=lambda t: t["first_ts"])
        # Total message + thread count for the round header.
        msg_total = sum(t["msg_count"] for t in thr_list)
        rounds.append({
            **meta,
            "thread_count": len(thr_list),
            "msg_count": msg_total,
            "threads": thr_list,
        })

    # Sort rounds chronologically.
    rounds.sort(key=lambda r: r.get("date") or "")
    return {
        "rounds": rounds,
        "total_threads": sum(r["thread_count"] for r in rounds),
        "total_messages": sum(r["msg_count"] for r in rounds),
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


_PERSON_REF = re.compile(r"\bperson:([A-Za-z0-9_-]+)")


def _clean_summary(text: str) -> str:
    """LLM-authored beat summaries sometimes reference people by their corpus
    ID (``person:cyphersnake``). Rewrite those as ``@cyphersnake`` so the
    narrative reads naturally."""
    return _PERSON_REF.sub(lambda m: f"@{m.group(1)}", text or "").strip()


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
                    "beat_summary": _clean_summary(b.get("summary") or ""),
                })
    return out


def _round_id_from_thread(thread: str, known_round_ids: list[str]) -> str:
    """Pull the canonical round id off the start of a gmail thread key.

    Thread keys are shaped like ``round:seed-pitch-investor:ribbit`` or
    ``round:starknet-grant-proposal``. Match each thread against the known
    round ids from finance.yaml (longest first) so `round:seed-pitch-...`
    folds into `round:seed`, not into `round:seed-pitch-investor`.
    """
    if not thread:
        return ""
    for rid in known_round_ids:
        if thread == rid or thread.startswith(rid + "-") or thread.startswith(rid + ":"):
            return rid
    return ""


def _pto_in_month(pto_by_pid: dict[str, list[dict]], ym: str,
                  people: list[dict]) -> list[dict]:
    """List PTO windows whose [start, end] overlaps month YM, with the
    person's handle attached so Page 3 can render 'Rajil1213 — summer pto
    Aug 9–18'."""
    out = []
    name_by_pid = {p["id"]: (p["full_name"], p["handle"]) for p in people}
    for pid, windows in (pto_by_pid or {}).items():
        nm = name_by_pid.get(pid)
        if not nm:
            continue
        full_name, handle = nm
        for w in windows or []:
            s, e = str(w.get("start", "")), str(w.get("end", ""))
            if not s or not e:
                continue
            if _ym(s) > ym or _ym(e) < ym:
                continue
            out.append({
                "handle": handle, "full_name": full_name,
                "start": s, "end": e,
                "kind": w.get("kind"), "label": w.get("label"),
            })
    # Sort by start date so consecutive vacations group naturally.
    return sorted(out, key=lambda w: (w["start"], w["handle"]))


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


def _person(pid: str, f: dict, e: dict, pat: dict,
            pto: list[dict] | None = None,
            travel: list[dict] | None = None) -> dict:
    handle = f.get("github_handle") or e.get("github_handle") or pid.removeprefix("person:")
    full_name = e.get("full_name") or f.get("full_name") or handle
    if full_name == "needs:review":
        full_name = handle
    role = f.get("role") or e.get("role") or "?"
    level = f.get("level") or e.get("level") or "ic"
    team = (f.get("team") or e.get("team") or "team:?")
    title = (f.get("title") or e.get("title") or "").strip()
    linkedin = (f.get("linkedin") or e.get("linkedin") or "").strip()
    started = f.get("started_at") or e.get("started_at")
    ended = f.get("ended_at") or e.get("ended_at")
    last_active = f.get("last_active") or e.get("last_active")
    commits = _safe_int(f.get("commits")) or _safe_int(e.get("commits")) or 0
    bio = (e.get("bio") or f.get("bio") or "").strip()
    location = (e.get("location") or "").strip()
    top_repos = list((f.get("top_repos") or e.get("top_repos") or []))
    pto = pto or []
    travel = travel or []

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

    # Cofounders carry a `title` field (e.g. "Cofounder & CEO") — render
    # that verbatim instead of the generic "Senior cofounder on Exec".
    if title:
        prefix = title
    else:
        prefix = f"{level_disp} {role_disp}"
        if team.startswith("team:") and team.removeprefix("team:") != "exec":
            prefix += f" on {team.removeprefix('team:').capitalize()}"
    behavior = (
        prefix
        + (f", based in {location}" if location else "")
        + f". Most active {_format_hours(peak, spread)}; {weekend_blurb}. "
        + f"{attend_blurb}, {resp_blurb}, {review_blurb}; "
        + f"{ship_blurb}."
    )

    # ---- status: active / winding down / departed -----------------------
    # Pre-departure decline window is 42 days (renderer linear-tapers from
    # 100% → 30% over those 6 weeks). Exposing this as an explicit status
    # so Page 2 can label "winding down (pre-departure window 2024-11-08 →
    # 2024-12-20)" — the exact thing Fyralis should detect in the data.
    decline_window: dict | None = None
    if ended:
        try:
            from datetime import date as _date, timedelta as _td
            end_d = _date.fromisoformat(str(ended)[:10])
            decline_window = {
                "start": (end_d - _td(days=42)).isoformat(),
                "end": end_d.isoformat(),
                "duration_days": 42,
                "anchor": "renderer linearly tapers message-emission probability from 1.0 → 0.3 across these 42 days",
            }
        except (ValueError, TypeError):
            pass
    status = ("departed" if ended else "active")

    # ---- pto roll-up: total days off per year + sick vs vacation split --
    pto_summary = _pto_summary(pto)

    return {
        "id": pid,
        "handle": handle,
        "full_name": full_name,
        "role": role,
        "title": title,
        "linkedin": linkedin,
        "level": level,
        "team": team,
        "started_at": started,
        "ended_at": ended,
        "last_active": last_active,
        "status": status,
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
        "decline_window": decline_window,
        "pto": pto,
        "pto_summary": pto_summary,
        "conference_travel": travel,
    }


def _pto_summary(pto: list[dict]) -> dict:
    """Roll up per-person PTO windows into a per-year tally + total days,
    so the per-person card can show "took 18 days off in 2024 (3 vacation
    blocks + 2 sick days)" without rendering every individual window."""
    from datetime import date as _date
    by_year: dict[str, dict[str, int]] = defaultdict(lambda: {
        "vacation_blocks": 0, "vacation_days": 0, "sick_days": 0,
    })
    total_days = 0
    for w in pto:
        try:
            s = _date.fromisoformat(str(w.get("start", ""))[:10])
            e = _date.fromisoformat(str(w.get("end", ""))[:10])
        except (ValueError, TypeError):
            continue
        days = (e - s).days + 1
        y = str(s.year)
        total_days += days
        if w.get("kind") == "sick":
            by_year[y]["sick_days"] += days
        else:
            by_year[y]["vacation_blocks"] += 1
            by_year[y]["vacation_days"] += days
    return {"total_days": total_days, "by_year": dict(by_year)}


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
        f"founded in {company.get('founded', '2024')[:4]}, building toward "
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
    external_events: list[dict] | None = None,
    departures: list[dict] | None = None,
    pto_count: int = 0,
    phase_title: str = "",
) -> str:
    """Two to four sentences of synthesised prose describing what's happening
    this month. The structured sections of the UI (milestone list, hires list,
    PTO list, active threads) carry the raw facts; this paragraph is the
    headline — what would a Fyralis evaluator describe if asked to summarise
    the month?"""
    external_events = external_events or []
    departures = departures or []
    total = sum(sig.values())
    has_engineering = bool(top_repos) and any(n > 0 for _, n in top_repos)

    sentences: list[str] = []

    # ---- 1) Opening sentence: lead with the strongest fact of the month ----
    opener = _month_opener(milestones, external_events, departures, new_hires,
                           phase_title, has_engineering, total, top_actors, people)
    sentences.append(opener)

    # ---- 2) Work in flight — threads + code focus ------------------------
    work = _month_work_sentence(beats, top_repos, has_engineering, phase_title)
    if work:
        sentences.append(work)

    # ---- 3) People-side activity (hires/departures/PTO) ------------------
    ppl = _month_people_sentence(new_hires, departures, pto_count, milestones)
    if ppl:
        sentences.append(ppl)

    # ---- 4) Top contributors + tempo -------------------------------------
    tempo = _month_tempo_sentence(top_actors, people, total, sig, phase_title)
    if tempo:
        sentences.append(tempo)

    return " ".join(s for s in sentences if s)


# ---- _month_narrative helpers --------------------------------------------

_PHASE_SHORT = {
    "Pre-product foundations":          "pre-product",
    "Public launch & technical thesis": "post-launch",
    "Strategic round & Strata maturation": "post-funding",
    "Testnet + Glock public release":   "testnet & Glock",
    "Mosaic build-out & Prague support":"Mosaic build-out",
    "Mosaic public + Bitcoin Dollar":   "Mosaic public",
    "Future":                           "future",
}


def _month_opener(milestones: list[dict],
                  external: list[dict],
                  departures: list[dict],
                  hires: list[dict],
                  phase: str,
                  has_eng: bool,
                  total: int,
                  top_actors: list[tuple[str, int]],
                  people: list[dict]) -> str:
    phase_short = _PHASE_SHORT.get(phase, phase.lower())

    if milestones:
        if len(milestones) == 1:
            m = milestones[0]
            return f"Headline event: {m.get('title', 'milestone')} ({m.get('date', '')})."
        titles = " and ".join(_trim(m.get("title", "?"), 60) for m in milestones[:2])
        return f"Two ship moments stack up this month — {titles}."

    high_impact = [e for e in external
                   if e.get("impact") in ("response_required", "high_attention")]
    if high_impact:
        ev = high_impact[0]
        impact_phrasing = {
            "response_required": "needs a response from the team",
            "high_attention":    "soaks up team attention",
        }.get(ev.get("impact"), "")
        return f"{ev.get('label', 'External event')} ({ev.get('date', '')}) {impact_phrasing}."

    if departures:
        d = departures[0]
        return (f"{d['full_name']} wraps up at Alpen this month "
                f"(left {d.get('ended_at', '')}).")

    if len(hires) >= 2:
        teams_set = sorted({h["team"].removeprefix("team:") for h in hires})
        return (f"Hiring month — {len(hires)} new joiners onboard across "
                f"{', '.join(teams_set)}.")

    if len(hires) == 1:
        h = hires[0]
        return f"{h['full_name']} joins as {h['role']} on {h['team'].removeprefix('team:')}."

    # Steady-state — anchor in phase
    if not has_eng and phase == "Pre-product foundations":
        return ("Pre-product foundations: company exists on paper, the four "
                "cofounders running fundraising prep, whitepaper drafting, and "
                "early protocol design — no public repos open yet.")
    if total == 0:
        return f"No corpus activity this month ({phase_short})."
    return f"Steady-state {phase_short}, no headline events this month."


def _month_work_sentence(beats: list[dict],
                         top_repos: list[tuple[str, int]],
                         has_eng: bool,
                         phase: str) -> str:
    if not beats and not top_repos:
        return ""

    # Pull up to two distinct threads (the structured "Active threads" block
    # below lists them all; this is the synthesis, so 2 is enough).
    if beats:
        seen, top = set(), []
        for b in beats:
            if b["thread_id"] in seen:
                continue
            seen.add(b["thread_id"])
            top.append(b)
            if len(top) == 2:
                break
        if len(top) == 1:
            b = top[0]
            sentence = (f"{b['thread_title']} is in {b['beat_kind'] or 'flight'}: "
                        f"{_trim(b['beat_summary'], 180)}")
        else:
            b1, b2 = top
            sentence = (f"Two threads progress in parallel — "
                        f"{b1['thread_title']} ({b1['beat_kind']}: "
                        f"{_trim(b1['beat_summary'], 110)}) and "
                        f"{b2['thread_title']} ({b2['beat_kind']}: "
                        f"{_trim(b2['beat_summary'], 110)}).")
        if top_repos and has_eng:
            tr = ", ".join(f"{r.removeprefix('repo:')} ({n})" for r, n in top_repos)
            sentence = sentence.rstrip(".") + f". Code piles into {tr}."
        return sentence

    # No beat data, but engineering activity — describe the work via repos.
    if has_eng:
        tr = ", ".join(f"{r.removeprefix('repo:')} ({n} commits)"
                       for r, n in top_repos[:3])
        return f"Code activity concentrates in {tr}."
    return ""


def _month_people_sentence(hires: list[dict],
                           departures: list[dict],
                           pto_count: int,
                           milestones: list[dict]) -> str:
    parts = []
    # If the opener already used hires/departures, restate them here only for
    # additional detail beyond a single name.
    if hires and milestones:
        names = ", ".join(h["full_name"] for h in hires[:3])
        more = f" and {len(hires) - 3} others" if len(hires) > 3 else ""
        parts.append(f"{names}{more} onboard alongside the launch")
    if departures and len(departures) >= 1 and not milestones:
        # Already used in opener — skip restating.
        pass

    if pto_count >= 12:
        parts.append(f"heavy PTO month ({pto_count} people out at some point)")
    elif pto_count >= 6:
        parts.append(f"moderate PTO load ({pto_count} people taking time off)")
    elif pto_count >= 3:
        parts.append(f"a few people on PTO ({pto_count})")

    if not parts:
        return ""
    return _capitalise(", ".join(parts)) + "."


def _month_tempo_sentence(top_actors: list[tuple[str, int]],
                          people: list[dict],
                          total: int,
                          sig: dict[str, int],
                          phase: str) -> str:
    if total == 0 or not top_actors:
        return ""

    # Volume framing — anchored in phase so readers know if this is loud or
    # quiet for that era.
    by_prov = sorted(sig.items(), key=lambda kv: -kv[1])
    dominant = by_prov[0]
    second   = by_prov[1] if len(by_prov) > 1 else None

    # Top contributors
    names = []
    for pid, n in top_actors[:3]:
        handle = _handle_for_pid(people, pid)
        names.append(f"{handle} ({n})")

    if dominant[0] == "github":
        what = f"engineering output — {dominant[1]} github events"
    elif dominant[0] == "slack":
        what = f"discussion-heavy — {dominant[1]} slack messages"
    elif dominant[0] == "calendar":
        what = f"meeting-heavy — {dominant[1]} calendar events"
    elif dominant[0] == "notion":
        what = f"docs-heavy — {dominant[1]} notion edits"
    else:
        what = f"{dominant[1]} {dominant[0]} events"

    if second and second[1] > 0:
        what += f", {second[1]} {second[0]}"

    if names:
        return f"{total:,} unique signals total — {what}; top voices: {', '.join(names)}."
    return f"{total:,} unique signals total — {what}."


def _capitalise(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s
