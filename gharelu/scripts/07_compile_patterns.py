#!/usr/bin/env python3
"""07_compile_patterns.py — derive per-person behavioral patterns from voice profile + role + commit history.

Deterministic, no LLM. Output is ``facts/patterns.yaml`` keyed by ``person:<handle>``
and consumed by ``05_render_events.py`` when rendering Slack/Jira/Calendar events.

These are the patterns Fyralis's model layer is supposed to *detect*:

  ship_lag_hours        — how late vs Jira issue planned end (negative = early)
  review_response_hours — how long PRs sit in this person's review queue
  msg_hour_peak         — 0-23, the hour of day they're most active
  msg_hour_spread       — std-dev hours around peak
  weekend_msg_factor    — 0.0 (no weekend work) → 1.5 (weekend warrior)
  standup_attendance    — 0.0 → 1.0 fraction of standups they show up to
  review_thoroughness   — 0.0 (rubber-stamp) → 1.0 (line-by-line)

We derive these from the voice profile (active_hours, reaction_to_bad_news,
typical_concerns), the person's role/team, and a deterministic hash so the
same handle produces the same pattern across reseeds.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
FACTS = ROOT / "facts" / "facts.yaml"
VOICES = ROOT / "facts" / "voices.yaml"
OUT = ROOT / "facts" / "patterns.yaml"


def _seed(handle: str) -> float:
    """Stable 0.0–1.0 from a handle."""
    h = hashlib.sha256(handle.encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _seed2(handle: str, salt: str) -> float:
    h = hashlib.sha256(f"{handle}:{salt}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _hours_peak_from_voice(active_hours: str, salt: float) -> tuple[int, float]:
    """Map active_hours string → (peak hour, spread). Salt adds per-person jitter."""
    base = {
        "mornings":       (9.0,  1.5),
        "evenings":       (19.0, 2.0),
        "late_night":     (23.0, 2.5),
        "normal":         (14.0, 3.0),
        "weekends_heavy": (15.0, 4.0),
    }.get(active_hours, (14.0, 3.0))
    peak = base[0] + (salt - 0.5) * 1.5   # ±0.75h jitter
    return int(round(peak)) % 24, base[1]


def _ship_lag(voice: dict, role: str, level: str, commits: int, salt: float) -> float:
    """Hours of slip vs planned ship date. Negative = early.

    Reaction to bad news drives the floor; tenure / level / role nudge it.
    A few people are designed-in chronic slippers; a few are designed-in
    ahead-of-plan shippers. The rest cluster mildly positive.
    """
    rxn = voice.get("reaction_to_bad_news", "")
    base = {
        "owns_and_debugs": 8,    # ~third of a day
        "deflects":        48,   # 2 days
        "escalates":       12,
        "goes_quiet":      36,
        "makes_a_joke":    20,
    }.get(rxn, 16)

    # Cofounders/seniors get more leeway — they batch ship.
    if "founder" in role.lower() or level in ("staff", "principal"):
        base -= 10
    if level == "junior":
        base += 12
    # High contrib volume → more flow → tighter ship cycle.
    if commits > 500:
        base -= 6
    elif commits < 30:
        base += 18

    # ±18h jitter per person, plus a long-tail kicker for 1-in-6 people.
    jitter = (salt - 0.5) * 36
    tail = 24 if salt > 0.83 else 0
    return round(base + jitter + tail, 1)


def _review_lag(voice: dict, role: str, salt: float) -> float:
    """Hours a PR sits in this person's review queue."""
    style = voice.get("style", "")
    base = {
        "terse":       4,
        "deadpan":     6,
        "technical": 12,
        "verbose":   18,
        "formal":    14,
        "warm":      10,
        "casual":     8,
        "chaotic":   28,
    }.get(style, 12)
    if "founder" in role.lower():
        base = max(2, base - 4)        # founders unblock fast
    if "research" in role.lower():
        base += 18                     # researchers go deep on math
    return round(base + (salt - 0.5) * 12, 1)


def _weekend_factor(voice: dict, salt: float) -> float:
    """0.0 (skip weekends) → 1.5 (weekend warrior)."""
    if voice.get("active_hours") == "weekends_heavy":
        return round(1.2 + salt * 0.4, 2)
    if voice.get("active_hours") == "late_night":
        return round(0.6 + salt * 0.4, 2)
    return round(0.15 + salt * 0.45, 2)


def _standup_attendance(voice: dict, role: str, level: str, salt: float) -> float:
    """0.4 → 1.0 fraction of standups attended."""
    base = 0.85
    if voice.get("style") == "chaotic":
        base = 0.55
    if level == "junior":
        base = max(0.6, base - 0.1)
    if "founder" in role.lower():
        base = min(1.0, base + 0.1)
    return round(max(0.4, min(1.0, base + (salt - 0.5) * 0.2)), 2)


def _review_thoroughness(voice: dict, salt: float) -> float:
    style = voice.get("style", "")
    base = {
        "technical": 0.85,
        "formal":    0.78,
        "deadpan":   0.7,
        "terse":     0.5,
        "casual":    0.55,
        "warm":      0.6,
        "verbose":   0.9,
        "chaotic":   0.4,
    }.get(style, 0.65)
    if "research" in (voice.get("typical_concerns") or []):
        base += 0.1
    return round(max(0.2, min(1.0, base + (salt - 0.5) * 0.15)), 2)


def main() -> None:
    facts = yaml.safe_load(FACTS.read_text())
    voices = (yaml.safe_load(VOICES.read_text()) or {}).get("voices") or {}
    out = {}
    for p in facts["people"]:
        pid = p["id"]
        handle = p["github_handle"]
        v = voices.get(pid, {}).get("voice", {})
        active = v.get("active_hours", "normal")
        salt_a = _seed2(handle, "hours")
        salt_s = _seed2(handle, "ship")
        salt_r = _seed2(handle, "review")
        salt_w = _seed2(handle, "weekend")
        salt_t = _seed2(handle, "thorough")
        salt_st = _seed2(handle, "standup")

        peak, spread = _hours_peak_from_voice(active, salt_a)

        out[pid] = {
            "ship_lag_hours":        _ship_lag(v, p["role"], p["level"], p["commits"], salt_s),
            "review_response_hours": _review_lag(v, p["role"], salt_r),
            "msg_hour_peak":         peak,
            "msg_hour_spread":       round(spread, 2),
            "weekend_msg_factor":    _weekend_factor(v, salt_w),
            "standup_attendance":    _standup_attendance(v, p["role"], p["level"], salt_st),
            "review_thoroughness":   _review_thoroughness(v, salt_t),
        }

    OUT.write_text(yaml.safe_dump({"patterns": out}, sort_keys=False,
                                   width=120, allow_unicode=True))
    # Console summary so the user can eyeball the distribution.
    print(f"wrote {OUT}  patterns={len(out)}", file=sys.stderr)
    print(f"\n{'handle':18s} {'ship_lag_h':>10s} {'review_h':>9s} "
          f"{'peak':>5s} {'wknd':>6s} {'standup':>7s} {'thorough':>9s}", file=sys.stderr)
    for p in facts["people"]:
        pat = out[p["id"]]
        print(f"{p['github_handle']:18s} {pat['ship_lag_hours']:>10.1f} "
              f"{pat['review_response_hours']:>9.1f} {pat['msg_hour_peak']:>5d} "
              f"{pat['weekend_msg_factor']:>6.2f} {pat['standup_attendance']:>7.2f} "
              f"{pat['review_thoroughness']:>9.2f}", file=sys.stderr)


if __name__ == "__main__":
    main()
