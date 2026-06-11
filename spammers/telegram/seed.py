"""Realistic Telegram corpus seeding.

Telegram is a NET-NEW Tier-C source: the frozen run has no Telegram corpus, so we
model a realistic account's dialogs + message history ourselves (the brief
sanctions this, like AWS synthesizing a CloudTrail stream). We project the run's
people into ONE Telegram user account's conversational surface — the single
signal Telegram ingestion has (messages in dialogs):

  * **channel** dialogs — a broadcast announcements channel (posts carry NO
    ``from_id``) + busy engineering supergroups (multi-sender; the hero dialog for
    a multi-page backward walk),
  * **chat** dialogs — small basic groups (founders / a sub-team),
  * **user** dialogs — 1:1 private chats with individual teammates (self-sent
    messages carry NO ``from_id`` — the other nuance the no-sender path exercises).

Everything is deterministic off the run seed + the org already in the run
(people → Telegram users). message_id is a per-dialog increasing sequence (the
backward-walk cursor + dedup grain), with dates increasing alongside; a handful of
messages carry an ``edit_date`` (the edit-versioned re-observe path). Idempotent:
a second call after the install row exists is a no-op.
"""
from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

import asyncpg

# Seed-stable install identity (hand these to the ingest-client / memory).
ACCOUNT_LABEL = "alpenlabs"
SESSION_STRING = "spam-telegram"   # matches Fyralis's spammer-mode preset
API_ID = "2040"
API_HASH = "b18441a1ff607e10a989891a5462e627"

_BACKFILL_DAYS = 430  # nominal density reference (~14 months)
# Fixed adoption date: the team's Telegram forms after the seed round / first
# hiring ramp. Anchoring here (instead of a rolling `now - 430d` window) means
# advancing virtual-now ACCUMULATES history forward and never predates adoption.
_ADOPTED = datetime(2024, 6, 1, tzinfo=timezone.utc)

# (kind, title, broadcast, target_messages). broadcast channels post with NO
# from_id; supergroups/groups/users are multi/▒ two-party.
_DIALOGS = [
    ("channel", "Alpen Labs Announcements", True, 44),
    ("channel", "eng-general", False, 620),       # the hero: multi-page backward walk
    ("channel", "incidents", False, 188),
    ("channel", "product-planning", False, 140),
    ("chat", "Founders", False, 122),
    ("chat", "Bridge Team", False, 160),
]
# Private 1:1 DMs are added per-person below.

_CHANNEL_POSTS = [
    "🚀 Mainnet checkpoint {n} finalized — proof verified on L1.",
    "We shipped the new prover pipeline today. Huge thanks to the team.",
    "Reminder: all-hands {when}. Agenda in the doc.",
    "New release cut: v0.{n}.0 is rolling out to validators.",
    "Welcome to the team, everyone joining this month! 🎉",
    "Postmortem for last week's bridge incident is published.",
]
_GROUP_LINES = [
    "can someone review my PR? bridge-relayer flake again",
    "deploying the checkpoint explorer to staging now",
    "the prover is OOMing on the big batch, looking into it",
    "merged. nice work on the SigV4 path",
    "standup in 5",
    "who owns the faucet rate-limit ticket?",
    "I think we should bump the page size cap to 100",
    "rebased onto main, CI is green",
    "the megagroup history walk is correct now, paging on offset_id",
    "ship it 🚢",
    "lunch?",
    "+1 to that approach",
    "found the bug — off-by-one in min_id",
    "great catch, pushing a fix",
]
_DM_LINES = [
    "hey, got a sec to pair on the bridge lag alarm?",
    "sure, jump on a call?",
    "can you take the on-call shift friday?",
    "yep, covered",
    "ETA on the prover fix?",
    "EOD today, just writing tests",
    "thanks for the review!",
    "lgtm 👍",
    "are you joining the offsite?",
    "wouldn't miss it",
]


def _uid(handle: str) -> int:
    """Stable 9-10 digit Telegram user id from a handle."""
    h = int(hashlib.blake2b(handle.encode(), digest_size=8).hexdigest(), 16)
    return 100_000_000 + (h % 8_900_000_000)


def _peer_id(title: str, kind: str) -> int:
    """Stable channel/chat id from a dialog title."""
    h = int(hashlib.blake2b((kind + ":" + title).encode(), digest_size=8).hexdigest(), 16)
    base = 1_000_000_000 if kind == "channel" else 200_000_000
    return base + (h % 800_000_000)


def _access_hash(seed_text: str) -> int:
    """Stable signed-64-bit access_hash."""
    h = int(hashlib.blake2b(seed_text.encode(), digest_size=8).hexdigest(), 16)
    return (h % (2 ** 63 - 1)) - (2 ** 62)  # spread across signed range


async def seed_telegram(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    at: Optional[datetime] = None,
) -> dict[str, int]:
    """Provision the install + realistic dialogs/messages for ``run_id``.

    Idempotent. Returns ``{"dialogs": D, "messages": M, "edits": E}`` (zeros if
    already seeded)."""
    existing = await pool.fetchval(
        "SELECT id FROM app_telegram.installations WHERE run_id = $1", run_id)
    if existing is not None:
        return {"dialogs": 0, "messages": 0, "edits": 0}

    seed_row = await pool.fetchrow(
        "SELECT seed, virtual_now FROM org.runs WHERE id = $1", run_id)
    if seed_row is None:
        raise LookupError(f"no run {run_id}")
    rng = random.Random(int(seed_row["seed"]) ^ 0x0074_6567)  # 'teg'
    now = at or seed_row["virtual_now"] or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    people = await pool.fetch(
        "SELECT id, handle, full_name FROM org.people WHERE run_id = $1 ORDER BY handle",
        run_id)
    handles = [p["handle"] for p in people] or ["founder", "eng1", "eng2", "ops"]
    # The self account = a stable teammate (the account whose history we read).
    self_handle = handles[0]
    self_user_id = _uid(self_handle)

    inst_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_telegram.installations
            (id, run_id, account_label, session_string, api_id, api_hash,
             self_user_id, self_username, self_phone, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
        inst_pk, run_id, ACCOUNT_LABEL, SESSION_STRING, API_ID, API_HASH,
        self_user_id, self_handle, "+15550000001", now)

    window_start = _ADOPTED if _ADOPTED < now else now - timedelta(days=14)
    span = max(1.0, (now - window_start).total_seconds())
    # Keep per-day message density ~constant as the window grows past 14 months.
    density = max(1.0, (now - window_start).days / _BACKFILL_DAYS)

    # Build the dialog plan: the named channels/groups + a 1:1 DM per (non-self)
    # person (capped so the corpus stays a realistic size).
    plan = list(_DIALOGS)
    dm_people = [h for h in handles if h != self_handle][:8]
    for h in dm_people:
        plan.append(("user", h, False, rng.randint(28, 84)))

    total_msgs = 0
    total_edits = 0
    for kind, title, broadcast, n_target in plan:
        if kind == "user":
            dialog_id = _uid(title)            # the peer is the other user
            access_hash = _access_hash("user:" + title)
            dlg_title = title
        else:
            dialog_id = _peer_id(title, kind)
            access_hash = _access_hash(kind + ":" + title) if kind == "channel" else None
            dlg_title = title

        dlg_pk = uuid4()
        await pool.execute(
            """INSERT INTO app_telegram.dialogs
                (id, install_pk, dialog_id, dialog_kind, access_hash, title, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7)""",
            dlg_pk, inst_pk, dialog_id, kind, access_hash, dlg_title, window_start)

        # Senders for this dialog.
        if kind == "user":
            participants = [self_handle, title]
        elif kind == "chat" and title == "Founders":
            participants = [h for h in handles[:4]]
            if self_handle not in participants:
                participants[0] = self_handle
        else:
            participants = handles

        # Deterministic ascending timestamps → message_id 1..N in date order.
        n = max(1, int(n_target * density))
        ts_list = sorted(
            window_start + timedelta(seconds=rng.uniform(0, span)) for _ in range(n))
        rows = []
        for i, when in enumerate(ts_list, start=1):
            if when >= now:
                when = now - timedelta(seconds=1)
            sender = rng.choice(participants)
            is_self = (sender == self_handle)
            if broadcast:
                # Channel broadcast: posted as the channel — NO from_id.
                from_user_id = None
                out = False
                text = rng.choice(_CHANNEL_POSTS).format(
                    n=rng.randint(100, 999),
                    when=(when + timedelta(days=1)).strftime("%a %H:%M"))
            elif kind == "user" and is_self:
                # Self-sent 1:1 message: commonly carries NO from_id (sender implicit).
                from_user_id = None
                out = True
                text = rng.choice(_DM_LINES)
            else:
                from_user_id = _uid(sender)
                out = is_self
                pool_lines = _DM_LINES if kind == "user" else _GROUP_LINES
                text = rng.choice(pool_lines)

            edit_ts = None
            if rng.random() < 0.04:  # ~4% of messages were later edited
                edit_dt = when + timedelta(minutes=rng.randint(1, 240))
                if edit_dt < now:
                    edit_ts = int(edit_dt.timestamp())
                    total_edits += 1
            rows.append((uuid4(), dlg_pk, i, int(when.timestamp()), edit_ts,
                         text, out, from_user_id, when))

        for r in rows:
            await pool.execute(
                """INSERT INTO app_telegram.messages
                    (id, dialog_pk, message_id, date_ts, edit_date_ts, text, out,
                     from_user_id, created_at, is_historical)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,TRUE)""",
                *r)
        total_msgs += len(rows)

    return {"dialogs": len(plan), "messages": total_msgs, "edits": total_edits}
