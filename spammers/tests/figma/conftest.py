"""Fixtures for the Figma mock fidelity suite (versions + comments + enum + webhook).

Seeds a deterministic team with:
  * three USERS (two contributors + the /v1/me service account that carries email);
  * two PROJECTS, two FILES;
  * the hero file (F1) carries FIVE versions (ids 1000..1004) so the suite can walk
    the CURSOR ``page_size``/``before`` pagination at page_size=2 (3 pages) and pin
    the ``{versions, pagination:{prev_page,next_page}}`` envelope + the full-URL links;
  * a COMMENT thread on F1 (root + reply + resolved) so the suite can pin the
    ``{comments:[…]}`` no-pagination array, ``parent_id``, ``resolved_at`` and the
    ``client_meta`` Vector/FrameOffset anchors.

Wires the Figma ``state`` singleton + an ASGI client.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

TEAM_ID = "9000001"
TEAM_NAME = "Fidelity Design Co"
ACCESS_TOKEN = "figd_fidelity_token_abc123XYZ0000000000"
WEBHOOK_PASSCODE = "fgwh_fidelity_passcode_0000"
WEBHOOK_ID = "555001"

ME_ID = "999000999"
ME_EMAIL = "ingest-svc@fidelity.example"
U1_ID = "111000111"
U2_ID = "222000222"

P1_ID = "p1000001"
P2_ID = "p2000002"
F1_KEY = "FKEYaaaaaaaaaaaaaaaaaa"
F2_KEY = "FKEYbbbbbbbbbbbbbbbbbb"

VNOW = datetime(2026, 4, 1, tzinfo=timezone.utc)

# (version_seq, label, description) for F1 — newest-first is 1004..1000.
F1_VERSIONS = [
    (1000, "Initial wireframes", "Initial wireframes for Onboarding."),
    (1001, None, None),                               # auto-save (null label)
    (1002, "Design review", "Design review for Onboarding."),
    (1003, None, None),                               # auto-save
    (1004, "Final handoff", "Final handoff for Onboarding."),
]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def figma_run(pool) -> UUID:
    run_id = uuid4()
    await pool.execute(
        """INSERT INTO org.runs (id, size, runtime, seed, fyralis_tenant_id,
               fyralis_base_url, virtual_now, mode, speed_multiplier)
           VALUES ($1,'small','few_months',17,$2,'http://localhost:8000',$3,'frozen',1.0)""",
        run_id, uuid4(), VNOW)
    team_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_figma.teams
            (id, run_id, base_url, team_id, team_name, access_token, webhook_passcode,
             webhook_id, created_at)
           VALUES ($1,$2,'https://api.figma.com',$3,$4,$5,$6,$7,$8)""",
        team_pk, run_id, TEAM_ID, TEAM_NAME, ACCESS_TOKEN, WEBHOOK_PASSCODE,
        WEBHOOK_ID, VNOW)

    me_pk, u1_pk, u2_pk = uuid4(), uuid4(), uuid4()
    await pool.execute(
        """INSERT INTO app_figma.users
            (id, team_pk, figma_user_id, handle, img_url, email, is_me)
           VALUES ($1,$2,$3,'Fyralis Ingest','https://img/me',$4,TRUE)""",
        me_pk, team_pk, ME_ID, ME_EMAIL)
    await pool.execute(
        """INSERT INTO app_figma.users
            (id, team_pk, figma_user_id, handle, img_url, is_me)
           VALUES ($1,$2,$3,'Ada Lovelace','https://img/ada',FALSE)""",
        u1_pk, team_pk, U1_ID)
    await pool.execute(
        """INSERT INTO app_figma.users
            (id, team_pk, figma_user_id, handle, img_url, is_me)
           VALUES ($1,$2,$3,'Alan Turing','https://img/alan',FALSE)""",
        u2_pk, team_pk, U2_ID)

    p1_pk, p2_pk = uuid4(), uuid4()
    await pool.execute(
        "INSERT INTO app_figma.projects (id, team_pk, project_id, name, sort_key) "
        "VALUES ($1,$2,$3,'Product',0)", p1_pk, team_pk, P1_ID)
    await pool.execute(
        "INSERT INTO app_figma.projects (id, team_pk, project_id, name, sort_key) "
        "VALUES ($1,$2,$3,'Brand',1)", p2_pk, team_pk, P2_ID)

    f1_pk, f2_pk = uuid4(), uuid4()
    await pool.execute(
        """INSERT INTO app_figma.files
            (id, team_pk, project_pk, file_key, name, thumbnail_url, editor_type,
             folder_name, creator_pk, current_version_id, last_modified, created_at, sort_key)
           VALUES ($1,$2,$3,$4,'Onboarding Flow','https://thumb/f1','figma','Product',
                   $5,'1004',$6,$7,0)""",
        f1_pk, team_pk, p1_pk, F1_KEY, u1_pk, VNOW, datetime(2025, 1, 1, tzinfo=timezone.utc))
    await pool.execute(
        """INSERT INTO app_figma.files
            (id, team_pk, project_pk, file_key, name, thumbnail_url, editor_type,
             folder_name, creator_pk, current_version_id, last_modified, created_at, sort_key)
           VALUES ($1,$2,$3,$4,'Brand Logo','https://thumb/f2','figma','Brand',
                   $5,'2000',$6,$7,0)""",
        f2_pk, team_pk, p2_pk, F2_KEY, u2_pk, VNOW, datetime(2025, 2, 1, tzinfo=timezone.utc))

    for i, (seq, label, desc) in enumerate(F1_VERSIONS):
        await pool.execute(
            """INSERT INTO app_figma.versions
                (id, file_pk, version_id, version_seq, label, description, user_pk,
                 created_at, is_historical)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,TRUE)""",
            uuid4(), f1_pk, str(seq), seq, label, desc, u1_pk if i % 2 == 0 else u2_pk,
            datetime(2025, 6, i + 1, tzinfo=timezone.utc))
    await pool.execute(
        """INSERT INTO app_figma.versions
            (id, file_pk, version_id, version_seq, label, description, user_pk,
             created_at, is_historical)
           VALUES ($1,$2,'2000',2000,'Logo v1','Logo v1.',$3,$4,TRUE)""",
        uuid4(), f2_pk, u2_pk, datetime(2025, 2, 2, tzinfo=timezone.utc))

    # Comments on F1: a root (Vector pin), a reply to it (FrameOffset), a resolved root.
    await pool.execute(
        """INSERT INTO app_figma.comments
            (id, file_pk, comment_id, parent_id, user_pk, message, order_id, client_meta,
             reactions, created_at, resolved_at, sort_key, is_historical)
           VALUES ($1,$2,'8001',NULL,$3,'Tighten the spacing here','000000001',
                   $4::jsonb,$5::jsonb,$6,NULL,1,TRUE)""",
        uuid4(), f1_pk, u1_pk, json.dumps({"x": 120.5, "y": 240.0}),
        json.dumps([{"user": {"id": U2_ID, "handle": "Alan Turing", "img_url": "https://img/alan"},
                     "emoji": ":+1:", "created_at": "2025-06-02T10:00:00Z"}]),
        datetime(2025, 6, 2, tzinfo=timezone.utc))
    await pool.execute(
        """INSERT INTO app_figma.comments
            (id, file_pk, comment_id, parent_id, user_pk, message, order_id, client_meta,
             reactions, created_at, resolved_at, sort_key, is_historical)
           VALUES ($1,$2,'8002','8001',$3,'Agreed, on it','000000002',
                   $4::jsonb,'[]'::jsonb,$5,NULL,2,TRUE)""",
        uuid4(), f1_pk, u2_pk,
        json.dumps({"node_id": "12:34", "node_offset": {"x": 10.0, "y": 20.0}}),
        datetime(2025, 6, 3, tzinfo=timezone.utc))
    await pool.execute(
        """INSERT INTO app_figma.comments
            (id, file_pk, comment_id, parent_id, user_pk, message, order_id, client_meta,
             reactions, created_at, resolved_at, sort_key, is_historical)
           VALUES ($1,$2,'8003',NULL,$3,'Ship it','000000003',
                   $4::jsonb,'[]'::jsonb,$5,$6,3,TRUE)""",
        uuid4(), f1_pk, u1_pk, json.dumps({"x": 0.0, "y": 0.0}),
        datetime(2025, 6, 4, tzinfo=timezone.utc),
        datetime(2025, 6, 10, tzinfo=timezone.utc))
    return run_id


@pytest_asyncio.fixture(loop_scope="session")
async def figma_client(pool, figma_run):
    from spammers.figma import state as f_state
    from spammers.figma.app import create_app, _FORCED_429

    f_state._STATE = f_state.FigmaMockState(pool=pool, run_id=figma_run)
    _FORCED_429["count"] = 0
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://mock") as c:
        yield c
    f_state._STATE = None


@pytest.fixture
def figma_auth() -> dict[str, str]:
    return {"X-Figma-Token": ACCESS_TOKEN}
