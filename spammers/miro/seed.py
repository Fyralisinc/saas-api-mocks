"""Realistic Miro corpus seeding.

Miro is a NET-NEW Tier-C source: the frozen run has no Miro corpus, so we model
realistic content ourselves (the brief sanctions this, like figma projecting the
design system and ashby projecting the org chart). Miro is the company's
**collaborative-whiteboard surface**, so we project the run's people into a Miro
org:

  * one **ORG** (the tenant) named after the company + a service-account board
    member (the ingesting integration, the boards' ``currentUserMembership``) +
    one Miro USER per person (board owners and item authors);
  * a handful of **BOARDS** (whiteboards: roadmap, architecture, retro, …) the
    org token can enumerate via ``GET /v2/boards`` (offset-paged); the first
    "hero" board carries enough ITEMS to cross the items page-size so the CURSOR
    walk on ``GET /v2/boards/{id}/items`` is genuinely multi-page;
  * per board a set of **ITEMS** — sticky notes, text, shapes, cards and frames
    (some items parented to a frame) — the single Miro signal Fyralis ingests.

Items have NO version field (Miro exposes only ``createdAt``/``modifiedAt``);
``item_seq`` is a monotonic integer the opaque cursor encodes. Everything is
deterministic off the run seed. Idempotent: a second call after the org row exists
is a no-op.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from random import Random
from typing import Optional
from uuid import UUID, uuid4

import asyncpg

# Seed-stable identity (hand these to the ingest-client / memory).
ORG_NAME = "Alpen Labs Inc."
ORG_ID = "3074457350000000001"
TEAM_ID = "3074457345618265000"
TEAM_NAME = "Alpen Labs"
ACCESS_TOKEN = "miro_oauth_eyJtaXJvIjoiYWxwZW4tbGFicy1ib2FyZHMtcmVhZCJ9"
SERVICE_ME_NAME = "Fyralis Ingest"

# (board name, item count) — the first board is the multi-page "hero".
_BOARDS = [
    ("Q3 Roadmap Planning", 120),
    ("System Architecture", 28),
    ("Sprint Retro — June", 22),
    ("User Journey Map", 18),
    ("Feature Brainstorm", 31),
    ("Org & Hiring Plan", 14),
]
_STICKY_TEXTS = [
    "Ship the new onboarding flow", "Cut p95 latency below 200ms",
    "Migrate to the v2 ingest pipeline", "Hire two backend engineers",
    "Customer interview takeaways", "Reduce cold-start time",
    "Roll out SSO for enterprise", "Deprecate the legacy API",
    "Improve docs coverage", "Add audit-log export", "Spike: vector search",
    "What about offline mode?", "Loop in legal on data retention",
    "Block: waiting on design", "Owner: platform team", "Due end of sprint",
    "Needs more discovery", "Quick win — low effort, high impact",
]
_TEXT_TEXTS = [
    "Now / Next / Later", "Goals for Q3", "Parking lot", "Decisions",
    "Risks & mitigations", "Open questions", "Definition of done",
]
_SHAPE_TEXTS = ["API Gateway", "Ingest Service", "Postgres", "Kafka",
                "Memory Fabric", "Webhook Edge", "", "Reconciler"]
_FRAME_TITLES = ["Now", "Next", "Later", "Backlog", "In Review", "Architecture",
                 "What went well", "What to improve"]
_CARD_TITLES = ["Onboarding redesign", "Latency budget", "v2 migration",
                "Enterprise SSO", "Audit log export", "Docs overhaul"]


def _numeric_id(rng: Random, digits: int = 19) -> str:
    first = rng.randint(1, 9)
    return str(first) + "".join(str(rng.randint(0, 9)) for _ in range(digits - 1))


def _board_id(rng: Random) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
    return "".join(rng.choice(alphabet) for _ in range(11)) + "="


async def seed_miro(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    at: Optional[datetime] = None,
) -> dict[str, int]:
    """Provision the org + users + boards + items.

    Idempotent. Returns ``{"boards": B, "items": I}``."""
    existing = await pool.fetchval(
        "SELECT id FROM app_miro.orgs WHERE run_id = $1", run_id)
    if existing is not None:
        return {"boards": 0, "items": 0}

    seed_row = await pool.fetchrow(
        "SELECT seed, virtual_now FROM org.runs WHERE id = $1", run_id)
    if seed_row is None:
        raise LookupError(f"no run {run_id}")
    rng = Random(int(seed_row["seed"]) ^ 0x6D69_726F)  # 'miro'
    now = at or seed_row["virtual_now"] or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    org_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_miro.orgs
            (id, run_id, base_url, org_id, org_name, team_id, team_name,
             access_token, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
        org_pk, run_id, "https://api.miro.com/v2", ORG_ID, ORG_NAME, TEAM_ID,
        TEAM_NAME, ACCESS_TOKEN, now - timedelta(days=900))

    # The service-account board member (the integration) — the currentUserMembership.
    me_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_miro.users
            (id, org_pk, person_id, miro_user_id, name, role, is_me)
           VALUES ($1,$2,NULL,$3,$4,'owner',TRUE)""",
        me_pk, org_pk, _numeric_id(rng), SERVICE_ME_NAME)

    people = await pool.fetch(
        "SELECT id, full_name, email, started_at FROM org.people "
        "WHERE run_id = $1 ORDER BY started_at, email", run_id)
    user_pks: list[UUID] = []
    for p in people:
        u_pk = uuid4()
        name = p["full_name"] or (p["email"] or "user").split("@")[0]
        await pool.execute(
            """INSERT INTO app_miro.users
                (id, org_pk, person_id, miro_user_id, name, role, is_me)
               VALUES ($1,$2,$3,$4,$5,'editor',FALSE)""",
            u_pk, org_pk, p["id"], _numeric_id(rng), name)
        user_pks.append(u_pk)
    if not user_pks:   # frozen run with no people — fall back to the service user
        user_pks = [me_pk]

    item_seq = 1_000_000
    boards = items = 0
    for bi, (bname, n_items) in enumerate(_BOARDS):
        b_pk = uuid4()
        bid = _board_id(rng)
        owner_pk = rng.choice(user_pks)
        born = now - timedelta(days=rng.randint(200, 700))
        # modified marches forward with the newest item; seeded then back-filled.
        last_mod = born
        await pool.execute(
            """INSERT INTO app_miro.boards
                (id, org_pk, board_id, name, description, view_link, owner_user_pk,
                 created_by_user_pk, modified_by_user_pk, created_at, modified_at,
                 last_opened_at, sort_key)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$7,$7,$8,$8,$9,$10)""",
            b_pk, org_pk, bid, bname,
            f"{bname} — collaborative working board.",
            f"https://miro.com/app/board/{bid}", owner_pk, born,
            now - timedelta(days=rng.randint(0, 14)), bi)
        boards += 1
        span_days = max((now - born).days - 3, 5)

        # A couple of frames first so later items can be parented to them.
        frame_ids: list[str] = []
        n_frames = rng.randint(1, 3)
        for _ in range(n_frames):
            item_seq += 1
            fid = _numeric_id(rng)
            created = born + timedelta(days=rng.randint(0, 3), hours=rng.randint(8, 18))
            author = rng.choice(user_pks)
            data = {"title": rng.choice(_FRAME_TITLES), "format": "custom",
                    "type": "freeform"}
            geometry = {"width": float(rng.randint(800, 1600)),
                        "height": float(rng.randint(600, 1200)), "rotation": 0.0}
            position = {"x": float(rng.randint(-2000, 2000)),
                        "y": float(rng.randint(-2000, 2000)),
                        "origin": "center", "relativeTo": "canvas_center"}
            await _ins_item(pool, b_pk, fid, "frame", data, geometry, position,
                            None, author, created, created, item_seq)
            frame_ids.append(fid)
            items += 1

        remaining = max(0, n_items - n_frames)
        for _ in range(remaining):
            item_seq += 1
            iid = _numeric_id(rng)
            created = born + timedelta(
                days=int(span_days * rng.random()), hours=rng.randint(8, 19),
                minutes=rng.randint(0, 59))
            # ~30% of items have been edited since creation (modifiedAt > createdAt).
            if rng.random() < 0.3:
                modified = created + timedelta(days=rng.randint(1, 20),
                                               hours=rng.randint(1, 8))
                modified = min(modified, now)
            else:
                modified = created
            author = rng.choice(user_pks)
            roll = rng.random()
            if roll < 0.62:
                itype = "sticky_note"
                data = {"content": rng.choice(_STICKY_TEXTS), "shape": "square"}
            elif roll < 0.74:
                itype = "text"
                data = {"content": rng.choice(_TEXT_TEXTS)}
            elif roll < 0.86:
                itype = "shape"
                data = {"content": rng.choice(_SHAPE_TEXTS),
                        "shape": rng.choice(["round_rectangle", "rectangle", "circle"])}
            else:
                itype = "card"
                data = {"title": rng.choice(_CARD_TITLES),
                        "description": rng.choice(_STICKY_TEXTS)}
            parent = rng.choice(frame_ids) if (frame_ids and rng.random() < 0.4) else None
            geometry = {"width": float(rng.randint(120, 320)),
                        "height": float(rng.randint(60, 220)),
                        "rotation": 0.0}
            position = {"x": round(rng.uniform(-2000, 2000), 2),
                        "y": round(rng.uniform(-2000, 2000), 2),
                        "origin": "center",
                        "relativeTo": "parent_top_left" if parent else "canvas_center"}
            await _ins_item(pool, b_pk, iid, itype, data, geometry, position,
                            parent, author, created, modified, item_seq)
            last_mod = max(last_mod, modified)
            items += 1

        await pool.execute(
            "UPDATE app_miro.boards SET modified_at = $2 WHERE id = $1", b_pk,
            max(last_mod, born))

    return {"boards": boards, "items": items}


async def _ins_item(pool, board_pk, item_id, item_type, data, geometry, position,
                    parent_id, author_pk, created, modified, item_seq) -> None:
    import json as _json
    await pool.execute(
        """INSERT INTO app_miro.items
            (id, board_pk, item_id, item_type, data, geometry, position, parent_id,
             created_by_user_pk, modified_by_user_pk, created_at, modified_at,
             item_seq, is_historical)
           VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7::jsonb,$8,$9,$9,$10,$11,$12,TRUE)""",
        uuid4(), board_pk, item_id, item_type, _json.dumps(data),
        _json.dumps(geometry), _json.dumps(position), parent_id, author_pk,
        created, modified, item_seq)
