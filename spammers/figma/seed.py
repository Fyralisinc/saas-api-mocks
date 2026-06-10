"""Realistic Figma corpus seeding.

Figma is a NET-NEW Tier-C source: the frozen run has no Figma corpus, so we model
realistic content ourselves (the brief sanctions this, like ashby projecting the
org chart and brex projecting card spend). Figma is the company's **design system
of record**, so we project the run's people into a Figma team:

  * one **TEAM** (the tenant) named after the company + a service-account **/v1/me**
    user (the ingesting integration) + one figma USER per person (version authors
    and commenters);
  * a few **PROJECTS** with **FILES** under them (the design surface a token can
    enumerate via teams → projects → files);
  * per file a **VERSION** history (named checkpoints + unlabelled auto-saves — the
    ``GET /v1/files/{key}/versions`` CURSOR stream; one "hero" file carries enough
    versions to cross the page-size so the ``before`` walk is genuinely multi-page)
    and a **COMMENT** thread (the ``GET /v1/files/{key}/comments`` un-paginated array,
    with replies + resolved + pin anchors).

A real backfill MERGES versions + comments into one event stream per file (there is
NO ``/events`` endpoint). ``version_seq`` is a monotonic integer (= numeric
``version_id``) so the cursor orders by it. Everything is deterministic off the run
seed. Idempotent: a second call after the team row exists is a no-op.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from random import Random
from typing import Optional
from uuid import UUID, uuid4

import asyncpg

# Seed-stable identity (hand these to the ingest-client / memory).
TEAM_NAME = "Alpen Labs Inc."
TEAM_ID = "1357924680135792468"
ACCESS_TOKEN = "figd_9kQ2vR7sLpX4nB1mW8tZ6yE3cA0dF5gH2jK7lN9"
WEBHOOK_PASSCODE = "fgwh_pass_4f1c9e7a52b84d06c1a9f4e2d7b605c8"
WEBHOOK_ID = "2208400"
SERVICE_ME_HANDLE = "Fyralis Ingest"
SERVICE_ME_EMAIL = "ingest-svc@alpenlabs.io"

_PROJECTS = ["Product Design", "Brand & Marketing", "Design System"]
_VERSION_LABELS = [
    "Initial wireframes", "Design review", "Polish pass", "Final handoff",
    "Dark mode", "Mobile layout", "Accessibility fixes", "v2 redesign",
    "Stakeholder feedback", "Copy updates", "Iconography", "Spacing audit",
]
_COMMENT_TEXTS = [
    "Can we tighten the spacing here?", "Love this direction 🔥",
    "Should this use the primary token?", "Nit: align to the 8px grid",
    "This copy needs legal review", "Approved — ship it",
    "Can you try a lighter weight?", "Missing the empty state",
    "What happens on hover?", "Let's reuse the card component",
    "Contrast is a bit low on the label", "Can we A/B this?",
]
_FILE_NAMES = [
    "Onboarding Flow", "Dashboard Redesign", "Mobile App", "Marketing Site",
    "Design Tokens", "Component Library", "Settings & Billing", "Email Templates",
    "Pricing Page", "Brand Guidelines",
]


def _numeric_id(rng: Random, digits: int = 13) -> str:
    first = rng.randint(1, 9)
    return str(first) + "".join(str(rng.randint(0, 9)) for _ in range(digits - 1))


def _file_key(rng: Random) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(rng.choice(alphabet) for _ in range(22))


async def seed_figma(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    at: Optional[datetime] = None,
) -> dict[str, int]:
    """Provision the team + users + projects + files + versions + comments.

    Idempotent. Returns ``{"files": F, "versions": V, "comments": C}``."""
    existing = await pool.fetchval(
        "SELECT id FROM app_figma.teams WHERE run_id = $1", run_id)
    if existing is not None:
        return {"files": 0, "versions": 0, "comments": 0}

    seed_row = await pool.fetchrow(
        "SELECT seed, virtual_now FROM org.runs WHERE id = $1", run_id)
    if seed_row is None:
        raise LookupError(f"no run {run_id}")
    rng = Random(int(seed_row["seed"]) ^ 0x6669_676D)  # 'figm'
    now = at or seed_row["virtual_now"] or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    team_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_figma.teams
            (id, run_id, base_url, team_id, team_name, access_token, webhook_passcode,
             webhook_id, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
        team_pk, run_id, "https://api.figma.com", TEAM_ID, TEAM_NAME, ACCESS_TOKEN,
        WEBHOOK_PASSCODE, WEBHOOK_ID, now - timedelta(days=900))

    # The /v1/me service account (the integration) — carries email (me-only).
    me_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_figma.users
            (id, team_pk, person_id, figma_user_id, handle, img_url, email, is_me)
           VALUES ($1,$2,NULL,$3,$4,$5,$6,TRUE)""",
        me_pk, team_pk, _numeric_id(rng), SERVICE_ME_HANDLE,
        f"https://s3-alpha.figma.com/profile/{_numeric_id(rng)}",
        SERVICE_ME_EMAIL)

    people = await pool.fetch(
        "SELECT id, handle, full_name, email, started_at FROM org.people "
        "WHERE run_id = $1 ORDER BY started_at, email", run_id)
    user_pks: list[tuple] = []   # (user_pk, started_at)
    for p in people:
        u_pk = uuid4()
        handle = p["full_name"] or (p["email"] or "user").split("@")[0]
        started = p["started_at"]
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        await pool.execute(
            """INSERT INTO app_figma.users
                (id, team_pk, person_id, figma_user_id, handle, img_url, email, is_me)
               VALUES ($1,$2,$3,$4,$5,$6,NULL,FALSE)""",
            u_pk, team_pk, p["id"], _numeric_id(rng), handle,
            f"https://s3-alpha.figma.com/profile/{_numeric_id(rng)}")
        user_pks.append((u_pk, started or (now - timedelta(days=700))))
    if not user_pks:   # frozen run with no people — fall back to the service user
        user_pks = [(me_pk, now - timedelta(days=700))]

    # Projects.
    project_pks: list[tuple] = []  # (project_pk, name)
    for i, pname in enumerate(_PROJECTS):
        pr_pk = uuid4()
        await pool.execute(
            """INSERT INTO app_figma.projects (id, team_pk, project_id, name, sort_key)
               VALUES ($1,$2,$3,$4,$5)""",
            pr_pk, team_pk, _numeric_id(rng, 8), pname, i)
        project_pks.append((pr_pk, pname))

    # Files — distribute across projects; the first file is the multi-page "hero".
    n_files = min(8, len(_FILE_NAMES))
    version_seq = 1_100_000_000_000
    comment_seq = 5_000_000_000
    files = versions = comments = 0
    for fi in range(n_files):
        pr_pk, pr_name = project_pks[fi % len(project_pks)]
        creator_pk, _ = rng.choice(user_pks)
        fname = _FILE_NAMES[fi]
        fkey = _file_key(rng)
        file_pk = uuid4()
        file_born = now - timedelta(days=rng.randint(300, 800))

        # Insert the file row FIRST (versions/comments FK to it); current_version_id
        # + last_modified are back-filled after the version walk below.
        await pool.execute(
            """INSERT INTO app_figma.files
                (id, team_pk, project_pk, file_key, name, thumbnail_url, editor_type,
                 folder_name, creator_pk, current_version_id, last_modified, created_at,
                 sort_key)
               VALUES ($1,$2,$3,$4,$5,$6,'figma',$7,$8,NULL,$9,$9,$10)""",
            file_pk, team_pk, pr_pk, fkey, fname,
            f"https://s3-alpha.figma.com/thumb/{fkey}", pr_name, creator_pk,
            file_born, fi)
        files += 1

        # Version count: the hero file crosses the 50/page cap; others are modest.
        n_versions = 58 if fi == 0 else rng.randint(3, 12)
        span_days = max((now - file_born).days - 5, 10)
        cur_version_id = None
        last_mod = file_born
        for vi in range(n_versions):
            version_seq += 1
            # Versions march forward in time across the file's life.
            created = file_born + timedelta(
                days=int(span_days * (vi + 1) / (n_versions + 1)),
                hours=rng.randint(8, 19), minutes=rng.randint(0, 59))
            author_pk, _ = rng.choice(user_pks)
            # ~45% are unlabelled auto-saves (label/description = NULL).
            if rng.random() < 0.45:
                label = description = None
            else:
                label = rng.choice(_VERSION_LABELS)
                description = f"{label} for {fname}."
            await pool.execute(
                """INSERT INTO app_figma.versions
                    (id, file_pk, version_id, version_seq, label, description, user_pk,
                     created_at, is_historical)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,TRUE)""",
                uuid4(), file_pk, str(version_seq), version_seq, label, description,
                author_pk, created)
            cur_version_id = str(version_seq)
            last_mod = max(last_mod, created)
            versions += 1

        # Back-fill the file's current_version_id (= newest version) + last_modified.
        await pool.execute(
            "UPDATE app_figma.files SET current_version_id=$2, last_modified=$3 WHERE id=$1",
            file_pk, cur_version_id, last_mod)

        # Comments — a thread with replies + resolved + pin anchors.
        n_comments = rng.randint(4, 14)
        roots: list[str] = []
        for ci in range(n_comments):
            comment_seq += 1
            cid = str(comment_seq)
            author_pk, _ = rng.choice(user_pks)
            created = file_born + timedelta(
                days=rng.randint(1, max(span_days, 2)), hours=rng.randint(8, 19))
            is_reply = roots and rng.random() < 0.35
            parent_id = rng.choice(roots) if is_reply else None
            resolved_at = (created + timedelta(days=rng.randint(1, 14))
                           if (not is_reply and rng.random() < 0.25) else None)
            # client_meta: a canvas Vector pin OR a frame-relative offset.
            if rng.random() < 0.6:
                client_meta = {"x": round(rng.uniform(0, 1440), 2),
                               "y": round(rng.uniform(0, 1024), 2)}
            else:
                client_meta = {"node_id": f"{rng.randint(1, 400)}:{rng.randint(1, 99)}",
                               "node_offset": {"x": round(rng.uniform(0, 320), 2),
                                               "y": round(rng.uniform(0, 240), 2)}}
            reactions: list = []
            if rng.random() < 0.2:
                ru_pk, _ = rng.choice(user_pks)
                ru = await pool.fetchrow(
                    "SELECT figma_user_id, handle, img_url FROM app_figma.users WHERE id=$1",
                    ru_pk)
                reactions = [{"user": {"id": ru["figma_user_id"], "handle": ru["handle"],
                                       "img_url": ru["img_url"]},
                              "emoji": rng.choice([":heart:", ":+1:", ":eyes:", ":fire:"]),
                              "created_at": (created + timedelta(hours=2)).strftime(
                                  "%Y-%m-%dT%H:%M:%SZ")}]
            import json as _json
            await pool.execute(
                """INSERT INTO app_figma.comments
                    (id, file_pk, comment_id, parent_id, user_pk, message, order_id,
                     client_meta, reactions, created_at, resolved_at, sort_key,
                     is_historical)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10,$11,$12,TRUE)""",
                uuid4(), file_pk, cid, parent_id, author_pk,
                rng.choice(_COMMENT_TEXTS), f"{ci + 1:09d}",
                _json.dumps(client_meta), _json.dumps(reactions), created, resolved_at,
                comment_seq)
            if not is_reply:
                roots.append(cid)
            comments += 1

    return {"files": files, "versions": versions, "comments": comments}
