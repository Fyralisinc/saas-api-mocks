"""Fixtures for the Miro mock fidelity suite (boards + items, two paginators).

Seeds a deterministic org with:
  * a service-account board member (the /currentUserMembership) + two users;
  * a Miro TEAM the boards reference;
  * the hero board B1 with SIX items (a frame + two sticky notes + text + shape +
    card) so the suite can walk the items CURSOR (``cursor``/``links.next``) at
    limit=2 (3 pages) and pin the ``{data,total,size,cursor,limit,links}`` envelope
    (NO top-level ``type``; ``cursor`` absent on the last page);
  * a second board B2 so ``GET /v2/boards`` (OFFSET) can be walked at limit=1 and
    the ``{data,total,size,offset,limit,links,type}`` envelope pinned.

Items carry ``geometry``/``position``/``parent``, item-scoped users with NO ``name``,
ms-precision Z timestamps and (on S1) ``modifiedAt`` > ``createdAt``.

Wires the Miro ``state`` singleton + an ASGI client.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

ORG_ID = "3074457350000000099"
ORG_NAME = "Fidelity Whiteboard Co"
TEAM_ID = "3074457345618265111"
TEAM_NAME = "Fidelity Team"
ACCESS_TOKEN = "miro_oauth_fidelitytoken0000000000000000"

ME_ID = "9990009990000000001"
U1_ID = "1110001110000000001"
U2_ID = "2220002220000000001"

B1_ID = "uXjVOD6abc1="
B2_ID = "uXjVOD6def2="

# (item_seq, item_id, type, data) for B1 — item_seq ascending = cursor order.
FR1_ID = "3458764500000000001"
S1_ID = "3458764500000000002"
S2_ID = "3458764500000000003"
T1_ID = "3458764500000000004"
SH1_ID = "3458764500000000005"
C1_ID = "3458764500000000006"

VNOW = datetime(2026, 4, 1, tzinfo=timezone.utc)
BORN = datetime(2025, 6, 1, 9, 0, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def miro_run(pool) -> UUID:
    run_id = uuid4()
    await pool.execute(
        """INSERT INTO org.runs (id, size, runtime, seed, fyralis_tenant_id,
               fyralis_base_url, virtual_now, mode, speed_multiplier)
           VALUES ($1,'small','few_months',23,$2,'http://localhost:8000',$3,'frozen',1.0)""",
        run_id, uuid4(), VNOW)
    org_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_miro.orgs
            (id, run_id, base_url, org_id, org_name, team_id, team_name,
             access_token, created_at)
           VALUES ($1,$2,'https://api.miro.com/v2',$3,$4,$5,$6,$7,$8)""",
        org_pk, run_id, ORG_ID, ORG_NAME, TEAM_ID, TEAM_NAME, ACCESS_TOKEN, BORN)

    me_pk, u1_pk, u2_pk = uuid4(), uuid4(), uuid4()
    await pool.execute(
        """INSERT INTO app_miro.users (id, org_pk, miro_user_id, name, role, is_me)
           VALUES ($1,$2,$3,'Fyralis Ingest','owner',TRUE)""",
        me_pk, org_pk, ME_ID)
    await pool.execute(
        """INSERT INTO app_miro.users (id, org_pk, miro_user_id, name, role, is_me)
           VALUES ($1,$2,$3,'Ada Lovelace','editor',FALSE)""",
        u1_pk, org_pk, U1_ID)
    await pool.execute(
        """INSERT INTO app_miro.users (id, org_pk, miro_user_id, name, role, is_me)
           VALUES ($1,$2,$3,'Alan Turing','editor',FALSE)""",
        u2_pk, org_pk, U2_ID)

    b1_pk, b2_pk = uuid4(), uuid4()
    await pool.execute(
        """INSERT INTO app_miro.boards
            (id, org_pk, board_id, name, description, view_link, owner_user_pk,
             created_by_user_pk, modified_by_user_pk, created_at, modified_at,
             last_opened_at, sort_key)
           VALUES ($1,$2,$3,'Q3 Roadmap','Roadmap planning board',
                   $4,$5,$5,$5,$6,$7,$7,0)""",
        b1_pk, org_pk, B1_ID, f"https://miro.com/app/board/{B1_ID}", u1_pk, BORN, VNOW)
    await pool.execute(
        """INSERT INTO app_miro.boards
            (id, org_pk, board_id, name, description, view_link, owner_user_pk,
             created_by_user_pk, modified_by_user_pk, created_at, modified_at,
             last_opened_at, sort_key)
           VALUES ($1,$2,$3,'System Architecture','Arch diagram',
                   $4,$5,$5,$5,$6,$7,$7,1)""",
        b2_pk, org_pk, B2_ID, f"https://miro.com/app/board/{B2_ID}", u2_pk, BORN, VNOW)

    geom = {"width": 200.0, "height": 80.0, "rotation": 0.0}
    pos = {"x": 100.0, "y": 100.0, "origin": "center", "relativeTo": "canvas_center"}
    pos_p = {"x": 10.0, "y": 20.0, "origin": "center", "relativeTo": "parent_top_left"}

    async def ins(board_pk, seq, iid, itype, data, parent, author, created, modified,
                  geometry=geom, position=pos):
        await pool.execute(
            """INSERT INTO app_miro.items
                (id, board_pk, item_id, item_type, data, geometry, position, parent_id,
                 created_by_user_pk, modified_by_user_pk, created_at, modified_at,
                 item_seq, is_historical)
               VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7::jsonb,$8,$9,$9,$10,$11,$12,TRUE)""",
            uuid4(), board_pk, iid, itype, json.dumps(data), json.dumps(geometry),
            json.dumps(position), parent, author, created, modified, seq)

    fc = datetime(2025, 6, 1, 10, tzinfo=timezone.utc)
    await ins(b1_pk, 1, FR1_ID, "frame",
              {"title": "Now", "format": "custom", "type": "freeform"}, None, u1_pk,
              fc, fc, geometry={"width": 1200.0, "height": 800.0, "rotation": 0.0})
    # S1: parented to the frame + edited after creation (modifiedAt > createdAt).
    await ins(b1_pk, 2, S1_ID, "sticky_note",
              {"content": "Ship onboarding", "shape": "square"}, FR1_ID, u1_pk,
              datetime(2025, 6, 2, 11, tzinfo=timezone.utc),
              datetime(2025, 6, 9, 14, tzinfo=timezone.utc), position=pos_p)
    await ins(b1_pk, 3, S2_ID, "sticky_note",
              {"content": "Cut p95 latency", "shape": "square"}, None, u2_pk,
              datetime(2025, 6, 3, 11, tzinfo=timezone.utc),
              datetime(2025, 6, 3, 11, tzinfo=timezone.utc))
    await ins(b1_pk, 4, T1_ID, "text", {"content": "Goals for Q3"}, None, u1_pk,
              datetime(2025, 6, 4, 11, tzinfo=timezone.utc),
              datetime(2025, 6, 4, 11, tzinfo=timezone.utc))
    await ins(b1_pk, 5, SH1_ID, "shape",
              {"content": "API Gateway", "shape": "round_rectangle"}, None, u2_pk,
              datetime(2025, 6, 5, 11, tzinfo=timezone.utc),
              datetime(2025, 6, 5, 11, tzinfo=timezone.utc))
    await ins(b1_pk, 6, C1_ID, "card",
              {"title": "Onboarding redesign", "description": "Q3 epic"}, None, u1_pk,
              datetime(2025, 6, 6, 11, tzinfo=timezone.utc),
              datetime(2025, 6, 6, 11, tzinfo=timezone.utc))
    # Six filler `text` items (seq 7-12) so B1 has 12 items: the items CURSOR can be
    # walked at limit=10 (Miro's real minimum) → 2 pages (10 + 2). The named items
    # above are the only sticky_note/frame/shape/card on B1 (filler is all `text`).
    for k in range(6):
        await ins(b1_pk, 7 + k, f"345876450000000010{k}", "text",
                  {"content": f"note {k}"}, None, u2_pk,
                  datetime(2025, 6, 7 + k, 12, tzinfo=timezone.utc),
                  datetime(2025, 6, 7 + k, 12, tzinfo=timezone.utc))
    # B2: two sticky notes.
    await ins(b2_pk, 13, "3458764500000000077", "sticky_note",
              {"content": "Postgres", "shape": "square"}, None, u2_pk,
              datetime(2025, 6, 7, 11, tzinfo=timezone.utc),
              datetime(2025, 6, 7, 11, tzinfo=timezone.utc))
    await ins(b2_pk, 14, "3458764500000000088", "sticky_note",
              {"content": "Kafka", "shape": "square"}, None, u2_pk,
              datetime(2025, 6, 8, 11, tzinfo=timezone.utc),
              datetime(2025, 6, 8, 11, tzinfo=timezone.utc))
    return run_id


@pytest_asyncio.fixture(loop_scope="session")
async def miro_client(pool, miro_run):
    from spammers.miro import state as m_state
    from spammers.miro.app import create_app, _FORCED_429

    m_state._STATE = m_state.MiroMockState(pool=pool, run_id=miro_run)
    _FORCED_429["count"] = 0
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://mock") as c:
        yield c
    m_state._STATE = None


@pytest.fixture
def miro_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {ACCESS_TOKEN}"}
