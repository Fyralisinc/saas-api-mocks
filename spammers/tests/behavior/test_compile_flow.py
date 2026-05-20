"""End-to-end OrgGen flow — what `spammer prepare` actually runs.

`prepare` creates a run and calls compile_run() to generate the org + project
the historical timeline into app_slack.messages. This must succeed for the
documented quickstart (`--seed=42`) to work at all.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from spammers.director.runs import create_run
from spammers.orggen.compile import compile_run

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.parametrize("seed", [42, 1, 7])
async def test_prepare_compiles_cleanly(pool, seed):
    # A fresh run in the shared schema; isolated by its own run_id.
    rid = await create_run(
        pool, size="small", runtime="few_months", seed=seed,
        fyralis_tenant_id=uuid4(), fyralis_base_url="http://localhost:8000",
    )
    # Should not raise. Currently raises UniqueViolationError on (channel_pk, ts)
    # because slack_ts() has only microsecond precision and the timeline can place
    # two messages in the same channel at the same instant.
    await compile_run(pool, rid)

    n = await pool.fetchval(
        "SELECT count(*) FROM app_slack.messages m "
        "JOIN app_slack.channels c ON c.id = m.channel_pk "
        "JOIN app_slack.workspaces w ON w.id = c.workspace_id "
        "WHERE w.run_id = $1",
        rid,
    )
    assert n > 0
