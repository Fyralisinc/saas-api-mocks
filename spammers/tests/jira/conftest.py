"""Fixtures for the Jira mock fidelity suite (Jira Cloud REST v3).

Reuses the session ``pool``; seeds a deterministic installation (its own
base_url + Basic-auth account_email/api_token), two users, one project (KEY=ENG),
and three issues with staggered ``updated_at`` — one carrying a status-transition
changelog (state_change) and comments. Wires the Jira ``state`` singleton + an
ASGI client; builds the Basic-auth header from the seeded credentials.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

BASE_URL = "https://acme.atlassian.net"
ACCOUNT_EMAIL = "ingest@acme.test"
API_TOKEN = "ATATTfidelitytoken000000000000000000000000"
PROJECT_KEY = "ENG"
ALICE_ACCT = "aaaa0000000000000000aaaa"
BOB_ACCT = "bbbb0000000000000000bbbb"
_T0 = datetime(2026, 1, 10, 9, 0, tzinfo=timezone.utc)


def _adf(text):
    return {"type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}


# (issue_id, key_num, summary, status, status_cat, updated_offset_h, has_status_transition)
ISSUES = [
    ("10001", 1, "Fix flaky retry", "Done", "done", 0, True),
    ("10002", 2, "Add pagination", "In Progress", "indeterminate", 2, False),
    ("10003", 3, "Investigate timeout", "To Do", "new", 4, False),
]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def jira_run(pool) -> UUID:
    run_id = uuid4()
    await pool.execute(
        """INSERT INTO org.runs (id, size, runtime, seed, fyralis_tenant_id,
               fyralis_base_url, virtual_now, mode, speed_multiplier)
           VALUES ($1,'small','few_months',7,$2,'http://localhost:8000',now(),'frozen',1.0)""",
        run_id, uuid4())
    inst_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_jira.installations
            (id, run_id, base_url, site_name, cloud_id, account_email, account_id,
             api_token, webhook_secret)
           VALUES ($1,$2,$3,'Acme','cloud-123',$4,$5,$6,'whsec')""",
        inst_pk, run_id, BASE_URL, ACCOUNT_EMAIL, ALICE_ACCT, API_TOKEN)
    for acct, email, name in ((ALICE_ACCT, "alice@acme.test", "Alice A"),
                              (BOB_ACCT, "bob@acme.test", "Bob B")):
        await pool.execute(
            """INSERT INTO app_jira.users (id, installation_pk, person_id, account_id, email, display_name)
               VALUES ($1,$2,NULL,$3,$4,$5)""",
            uuid4(), inst_pk, acct, email, name)
    proj_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_jira.projects (id, installation_pk, project_id, key, name, project_type_key, lead_account_id)
           VALUES ($1,$2,'40001',$3,'Engineering','software',$4)""",
        proj_pk, inst_pk, PROJECT_KEY, ALICE_ACCT)
    for iid, n, summary, status, cat, uoff, has_transition in ISSUES:
        created = _T0
        updated = _T0 + timedelta(hours=uoff)
        issue_pk = uuid4()
        await pool.execute(
            """INSERT INTO app_jira.issues
                (id, installation_pk, project_pk, issue_id, issue_key, summary, description,
                 issue_type, status, status_category, priority, resolution, resolution_date,
                 assignee_account_id, reporter_account_id, creator_account_id, labels, components,
                 story_points, created_at, updated_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,'Task',$8,$9,'Medium',$10,$11,
                       $12,$13,$13,'["backend"]'::jsonb,'[]'::jsonb,$14,$15,$16)""",
            issue_pk, inst_pk, proj_pk, iid, f"{PROJECT_KEY}-{n}", summary,
            json.dumps(_adf(f"Description for {summary}")), status, cat,
            "Done" if cat == "done" else None, updated if cat == "done" else None,
            BOB_ACCT, ALICE_ACCT, 3.0, created, updated)
        if has_transition:
            items = [{"field": "status", "fieldtype": "jira", "fieldId": "status",
                      "from": "1", "fromString": "To Do", "to": "3", "toString": status}]
            await pool.execute(
                """INSERT INTO app_jira.changelogs (id, issue_pk, history_id, author_account_id, items, created_at, position)
                   VALUES ($1,$2,'90001',$3,$4::jsonb,$5,0)""",
                uuid4(), issue_pk, ALICE_ACCT, json.dumps(items), updated)
            await pool.execute(
                """INSERT INTO app_jira.comments (id, issue_pk, comment_id, author_account_id, body, created_at, updated_at, position)
                   VALUES ($1,$2,'70001',$3,$4::jsonb,$5,$5,0)""",
                uuid4(), issue_pk, BOB_ACCT, json.dumps(_adf("Looks fixed, thanks!")), updated)
    return run_id


@pytest_asyncio.fixture(loop_scope="session")
async def jira_client(pool, jira_run):
    from spammers.jira import state as j_state
    from spammers.jira.app import create_app
    from spammers.jira.ratelimit import _RL

    j_state._STATE = j_state.JiraMockState(pool=pool, run_id=jira_run)
    _RL._buckets.clear()
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://mock") as c:
        yield c
    j_state._STATE = None


def basic_header(email: str = ACCOUNT_EMAIL, token: str = API_TOKEN) -> dict[str, str]:
    cred = base64.b64encode(f"{email}:{token}".encode()).decode()
    return {"Authorization": f"Basic {cred}", "Accept": "application/json",
            "Content-Type": "application/json"}


@pytest.fixture
def jira_auth() -> dict[str, str]:
    return basic_header()
