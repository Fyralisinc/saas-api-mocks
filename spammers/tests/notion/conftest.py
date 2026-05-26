"""Fixtures for the Notion mock fidelity suite (API version 2022-06-28).

Reuses the session ``pool``; seeds a deterministic integration, two people
(for /v1/users), two databases, three pages (rows) in the first database with
title properties, blocks under one page, and a comment — then wires the Notion
``state`` singleton + an ASGI client.
"""
from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

BOT_TOKEN = "ntn_fidelitytoken0000000000000000000000000000"
WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
BOT_USER_ID = "22222222-2222-4222-8222-222222222222"
VERIFICATION_TOKEN = "secret_fidelityverif00000000000000000000000"
DB1_ID = "33333333-3333-4333-8333-333333333331"
DB2_ID = "33333333-3333-4333-8333-333333333332"
PAGE_IDS = ["44444444-4444-4444-8444-44444444440%d" % i for i in (1, 2, 3)]


def _rt(content: str) -> list:
    return [{"type": "text", "text": {"content": content, "link": None},
             "annotations": {"bold": False, "italic": False, "strikethrough": False,
                             "underline": False, "code": False, "color": "default"},
             "plain_text": content, "href": None}]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def notion_run(pool) -> UUID:
    run_id = uuid4()
    await pool.execute(
        """INSERT INTO org.runs (id, size, runtime, seed, fyralis_tenant_id,
               fyralis_base_url, virtual_now, mode, speed_multiplier)
           VALUES ($1,'small','few_months',4,$2,'http://localhost:8000',now(),'frozen',1.0)""",
        run_id, uuid4())
    for handle, name, email in (("alice", "Alice A", "alice@n.test"), ("bob", "Bob B", "bob@n.test")):
        await pool.execute(
            """INSERT INTO org.people (id, run_id, handle, full_name, email, role, level, timezone, started_at)
               VALUES ($1,$2,$3,$4,$5,'engineer','mid','UTC',now())""",
            uuid4(), run_id, handle, name, email)
    integ_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_notion.integrations
            (id, run_id, bot_token, workspace_id, workspace_name, bot_user_id, bot_name,
             client_id, client_secret, verification_token)
           VALUES ($1,$2,$3,$4,'Fidelity WS',$5,'Ingest Bot','cid','csec',$6)""",
        integ_pk, run_id, BOT_TOKEN, WORKSPACE_ID, BOT_USER_ID, VERIFICATION_TOKEN)
    schema = {"Name": {"id": "title", "name": "Name", "type": "title", "title": {}}}
    db_pks = {}
    for did, title in ((DB1_ID, "Engineering Wiki"), (DB2_ID, "Tasks")):
        dpk = uuid4(); db_pks[did] = dpk
        await pool.execute(
            """INSERT INTO app_notion.databases
                (id, integration_pk, database_id, title, parent_type, parent_id, icon,
                 properties_schema, url, created_time, last_edited_time)
               VALUES ($1,$2,$3,$4,'workspace',NULL,'📘',$5::jsonb,$6,now(),now())""",
            dpk, integ_pk, did, title, json.dumps(schema), f"https://notion.so/{did}")
    for i, pid in enumerate(PAGE_IDS):
        props = {"Name": {"id": "title", "type": "title", "title": _rt(f"Doc {i+1}")}}
        page_pk = uuid4()
        await pool.execute(
            """INSERT INTO app_notion.pages
                (id, integration_pk, page_id, parent_type, parent_id, database_pk, title,
                 properties, icon, archived, url, created_by, created_time, last_edited_time)
               VALUES ($1,$2,$3,'database_id',$4,$5,$6,$7::jsonb,NULL,FALSE,$8,$9,now(),now())""",
            page_pk, integ_pk, pid, DB1_ID, db_pks[DB1_ID], f"Doc {i+1}",
            json.dumps(props), f"https://notion.so/{pid}", BOT_USER_ID)
        if i == 0:
            for pos, (btype, text) in enumerate([("heading_2", "Background"), ("paragraph", "Details here.")]):
                await pool.execute(
                    """INSERT INTO app_notion.blocks
                        (id, page_pk, block_id, parent_block_id, type, content, has_children,
                         position, created_by, created_time, last_edited_time)
                       VALUES ($1,$2,$3,NULL,$4,$5::jsonb,FALSE,$6,$7,now(),now())""",
                    uuid4(), page_pk, str(uuid4()), btype,
                    json.dumps({"rich_text": _rt(text), "color": "default"}), pos, BOT_USER_ID)
            await pool.execute(
                """INSERT INTO app_notion.comments
                    (id, page_pk, comment_id, discussion_id, parent_page_id, rich_text,
                     created_by, created_time, last_edited_time)
                   VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,now(),now())""",
                uuid4(), page_pk, str(uuid4()), str(uuid4()), pid,
                json.dumps(_rt("LGTM")), BOT_USER_ID)
    return run_id


@pytest_asyncio.fixture(loop_scope="session")
async def notion_client(pool, notion_run):
    from spammers.notion import state as n_state
    from spammers.notion.app import create_app
    from spammers.notion.ratelimit import _RL

    integ = await pool.fetchrow("SELECT * FROM app_notion.integrations WHERE run_id=$1", notion_run)
    n_state._STATE = n_state.NotionMockState(
        pool=pool, run_id=notion_run, integration_pk=integ["id"], bot_token=integ["bot_token"],
        bot_user_id=integ["bot_user_id"], bot_name=integ["bot_name"],
        workspace_id=integ["workspace_id"], workspace_name=integ["workspace_name"])
    _RL._buckets.clear()
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://mock") as c:
        yield c
    n_state._STATE = None


@pytest.fixture
def notion_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {BOT_TOKEN}", "Notion-Version": "2022-06-28"}
