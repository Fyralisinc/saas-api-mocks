"""Corpus-test fixtures.

Inherits the session-scoped ``pool`` from the parent conftest (which runs
migrations against the test DB). Overrides ``run_id`` to insert a corpus-
flavored ``org.runs`` row (function-scoped — each test gets a fresh run).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio(loop_scope="session")

FIXTURES = Path(__file__).parent / "fixtures"


@pytest_asyncio.fixture(loop_scope="session")
async def run_id(pool):
    rid = uuid4()
    await pool.execute(
        "INSERT INTO org.runs (id, size, runtime, seed, fyralis_tenant_id, fyralis_base_url, "
        "virtual_now, profile_kind, corpus_path) "
        "VALUES ($1, 'small', 'few_months', 1, $2, 'http://localhost', $3, 'corpus', $4)",
        rid, uuid4(), datetime(2024, 6, 1, tzinfo=timezone.utc),
        str(FIXTURES / "tiny.events.jsonl"),
    )
    yield rid
    # Tear down child-first: app_slack.messages.user_pk and app_slack.users.person_id
    # reference users/people WITHOUT ON DELETE CASCADE, so a bare run-delete trips
    # those FKs. Dropping channels first cascades messages away; dropping the
    # workspace then cascades its users, leaving the run-delete free to cascade
    # org.people.
    ws_filter = "workspace_id IN (SELECT id FROM app_slack.workspaces WHERE run_id = $1)"
    await pool.execute(f"DELETE FROM app_slack.channels WHERE {ws_filter}", rid)
    await pool.execute("DELETE FROM app_slack.workspaces WHERE run_id = $1", rid)
    await pool.execute("DELETE FROM org.runs WHERE id = $1", rid)


@pytest.fixture
def tiny_corpus() -> Path:
    return FIXTURES / "tiny.events.jsonl"
