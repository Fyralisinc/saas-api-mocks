"""Realistic Signal corpus seeding.

Signal is a NET-NEW Tier-C source: the frozen run has no Signal corpus, so we
model a realistic linked account's threads + message history ourselves (the brief
sanctions this, like AWS/Telegram). We project the run's people into ONE Signal
linked account's conversational surface — the single signal Signal ingestion has
(messages in threads):

  * **group** threads (keyed by a base64 groupId) — engineering / founders /
    incident groups (multi-sender; the hero thread for a multi-page backward walk),
  * **direct** threads (keyed by the other party's Signal uuid) — 1:1 chats with
    individual teammates (the linked account's OWN replies are self-sent: out=True,
    a ``syncMessage.sentMessage`` carrying NO first-class sender → source_actor_ref
    None on the handler).

Everything is deterministic off the run seed + the org already in the run (people
→ Signal contacts). A message's id IS its ``timestamp`` in MILLISECONDS (Signal has
no separate integer id) — strictly increasing within a thread (the backward-walk
cursor + dedup grain). Signal v1 has NO edits → messages are immutable (the dedup
edit slot is always ``none``). Idempotent: a second call after the install row
exists is a no-op.

NB (logged divergence): real signal-cli CANNOT fetch deep history at all — it is
forward-only (the server holds no plaintext archive). This deep corpus serves the
Fyralis ``get_history`` CONTRACT (which assumes a backward-walkable history); the
"Signal is architecturally live-only" reality is logged, not modelled away.
"""
from __future__ import annotations

import base64
import hashlib
import random
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

import asyncpg

# Seed-stable install identity (hand these to the ingest-client / memory).
ACCOUNT_LABEL = "alpenlabs"
SESSION_STRING = "spam-signal"   # matches Fyralis's spammer-mode preset
ACCOUNT_NUMBER = "+15550100001"

_BACKFILL_DAYS = 430  # ~14 months of history

# (kind, title, n_target). Groups are multi-sender; the hero is a multi-page walk.
_GROUPS = [
    ("group", "Eng War Room", 624),       # the hero: multi-page backward walk
    ("group", "Founders", 132),
    ("group", "Incident Response", 188),
    ("group", "Bridge Team", 150),
]
# Private 1:1 direct threads are added per-person below.

_GROUP_LINES = [
    "can someone review my PR? bridge-relayer flake again",
    "deploying the checkpoint explorer to staging now",
    "the prover is OOMing on the big batch, looking into it",
    "merged. nice work on the SigV4 path",
    "standup in 5",
    "who owns the faucet rate-limit ticket?",
    "I think we should bump the page size cap to 100",
    "rebased onto main, CI is green",
    "the group history walk is correct now, paging on timestamp",
    "ship it 🚢",
    "lunch?",
    "+1 to that approach",
    "found the bug — off-by-one in the min_ts floor",
    "great catch, pushing a fix",
    "moving this to Signal since it's sensitive",
    "ack, on it",
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
    "keeping this off corp channels — heads up on the vendor deal",
    "understood, let's discuss live",
]


def _signal_uuid(handle: str) -> str:
    """Stable Signal ACI uuid (a v4-shaped UUID) from a handle."""
    h = hashlib.blake2b(("signal-aci:" + handle).encode(), digest_size=16).digest()
    return str(UUID(bytes=h, version=4))


def _number(handle: str) -> str:
    """Stable E.164 phone from a handle."""
    h = int(hashlib.blake2b(("e164:" + handle).encode(), digest_size=8).hexdigest(), 16)
    return "+1555" + f"{h % 10_000_000:07d}"


def _group_id(title: str) -> str:
    """Stable base64 groupId (32-byte master-key-derived id, like real Signal)."""
    raw = hashlib.blake2b(("group:" + title).encode(), digest_size=32).digest()
    return base64.b64encode(raw).decode()


async def seed_signal(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    at: Optional[datetime] = None,
) -> dict[str, int]:
    """Provision the install + realistic threads/messages for ``run_id``.

    Idempotent. Returns ``{"threads": T, "messages": M, "self_sent": S}`` (zeros if
    already seeded)."""
    existing = await pool.fetchval(
        "SELECT id FROM app_signal.installations WHERE run_id = $1", run_id)
    if existing is not None:
        return {"threads": 0, "messages": 0, "self_sent": 0}

    seed_row = await pool.fetchrow(
        "SELECT seed, virtual_now FROM org.runs WHERE id = $1", run_id)
    if seed_row is None:
        raise LookupError(f"no run {run_id}")
    rng = random.Random(int(seed_row["seed"]) ^ 0x0073_6967)  # 'sig'
    now = at or seed_row["virtual_now"] or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    people = await pool.fetch(
        "SELECT id, handle, full_name FROM org.people WHERE run_id = $1 ORDER BY handle",
        run_id)
    handles = [p["handle"] for p in people] or ["founder", "eng1", "eng2", "ops"]
    name_of = {p["handle"]: (p["full_name"] or p["handle"]) for p in people}
    # The self account = a stable teammate (the account whose threads we read).
    self_handle = handles[0]
    self_uuid = _signal_uuid(self_handle)

    inst_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_signal.installations
            (id, run_id, account_label, session_string, account_number, account_uuid,
             account_username, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
        inst_pk, run_id, ACCOUNT_LABEL, SESSION_STRING, ACCOUNT_NUMBER, self_uuid,
        self_handle, now)

    window_start = now - timedelta(days=_BACKFILL_DAYS)
    span = (now - window_start).total_seconds()

    # Build the thread plan: the named groups + a 1:1 direct thread per (non-self)
    # person (capped so the corpus stays a realistic size).
    plan: list[tuple[str, str, int]] = list(_GROUPS)
    dm_people = [h for h in handles if h != self_handle][:8]
    for h in dm_people:
        plan.append(("direct", h, rng.randint(26, 78)))

    total_msgs = 0
    total_self = 0
    for kind, title, n_target in plan:
        if kind == "direct":
            thread_id = _signal_uuid(title)        # the peer's Signal uuid
            thread_title = name_of.get(title, title)
            participants = [self_handle, title]
            group_rev = None
        else:
            thread_id = _group_id(title)            # base64 groupId
            thread_title = title
            participants = handles
            group_rev = rng.randint(2, 9)            # the group's current revision

        thr_pk = uuid4()
        await pool.execute(
            """INSERT INTO app_signal.threads
                (id, install_pk, thread_id, thread_kind, thread_title, created_at)
               VALUES ($1,$2,$3,$4,$5,$6)""",
            thr_pk, inst_pk, thread_id, kind, thread_title, window_start)

        # Strictly-increasing ms timestamps → unique per-thread message ids.
        n = max(1, n_target)
        raw_ts = sorted(window_start + timedelta(seconds=rng.uniform(0, span))
                        for _ in range(n))
        rows = []
        last_ms = 0
        for when in raw_ts:
            if when >= now:
                when = now - timedelta(seconds=1)
            ts_ms = int(when.timestamp() * 1000)
            if ts_ms <= last_ms:
                ts_ms = last_ms + 1
            last_ms = ts_ms
            when = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)

            sender = rng.choice(participants)
            is_self = (sender == self_handle)
            pool_lines = _DM_LINES if kind == "direct" else _GROUP_LINES
            body = rng.choice(pool_lines)
            if is_self:
                # self-sent (own/outgoing): out=True, NO first-class sender.
                sender_uuid = None
                sender_number = None
                sender_name = None
                out = True
                total_self += 1
            else:
                sender_uuid = _signal_uuid(sender)
                sender_number = _number(sender)
                sender_name = name_of.get(sender, sender)
                out = False
            rows.append((uuid4(), thr_pk, ts_ms, sender_uuid, sender_number,
                         sender_name, body, out, group_rev, when))

        for r in rows:
            await pool.execute(
                """INSERT INTO app_signal.messages
                    (id, thread_pk, ts_ms, sender_uuid, sender_number, sender_name,
                     body, out, group_revision, created_at, is_historical)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,TRUE)""",
                *r)
        total_msgs += len(rows)

    return {"threads": len(plan), "messages": total_msgs, "self_sent": total_self}
