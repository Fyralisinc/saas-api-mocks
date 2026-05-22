"""Outbound GitHub webhooks: live events are signed + delivered; historical aren't."""
from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest
import respx

from spammers.common.signing import github_verify
from spammers.director.orchestrator import EmissionLoop
from spammers.orggen.live import inject_github_event
from spammers.tests.github.conftest import ACCOUNT_LOGIN

pytestmark = pytest.mark.asyncio(loop_scope="session")

WEBHOOK_URL = "https://consumer.test/webhooks/github"
SECRET = "whsec"  # seeded app webhook_secret


async def test_historical_event_not_emitted(pool, gh_run):
    # is_historical=TRUE events are pull-only — never webhooked.
    await pool.execute(
        """
        INSERT INTO timeline.events (id, run_id, virtual_ts, type, actor_id, payload, is_historical)
        VALUES (gen_random_uuid(), $1, now() - interval '1 day', 'github.pull_request',
                (SELECT id FROM org.people WHERE run_id=$1 LIMIT 1),
                '{"action":"opened","repo":"acme/core","number":1}'::jsonb, TRUE)
        """,
        gh_run,
    )
    loop = EmissionLoop(pool, gh_run, github_events_url=WEBHOOK_URL)
    with respx.mock:
        route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))
        await loop._drain_once()
    assert route.call_count == 0


async def test_live_pull_request_delivered_and_signed(pool, gh_run):
    await inject_github_event(pool, gh_run, kind="pull_request", repo="core", handle="octocat")
    loop = EmissionLoop(pool, gh_run, github_events_url=WEBHOOK_URL)
    with respx.mock:
        route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))
        await loop._drain_once()

    assert route.call_count == 1
    req = route.calls[0].request
    assert req.headers["X-GitHub-Event"] == "pull_request"
    assert req.headers["X-GitHub-Delivery"]
    assert req.headers["X-GitHub-Hook-Installation-Target-Type"] == "integration"
    # Signature verifies against the app webhook secret over the exact bytes sent.
    assert github_verify(SECRET, req.headers["X-Hub-Signature-256"], req.content)

    body = json.loads(req.content)
    assert body["action"] == "opened"
    assert body["pull_request"]["state"] == "open"
    assert body["repository"]["full_name"] == f"{ACCOUNT_LOGIN}/core"
    assert body["sender"]["login"] == "octocat"
    assert "installation" in body and "id" in body["installation"]


async def test_live_issue_delivered(pool, gh_run):
    await inject_github_event(pool, gh_run, kind="issues", repo="web", handle="octocat")
    loop = EmissionLoop(pool, gh_run, github_events_url=WEBHOOK_URL)
    with respx.mock:
        route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))
        await loop._drain_once()

    # Only the freshly-injected (unemitted) issue event drains here.
    assert route.call_count == 1
    req = route.calls[0].request
    assert req.headers["X-GitHub-Event"] == "issues"
    body = json.loads(req.content)
    assert body["issue"]["state"] == "open"
    assert body["repository"]["full_name"] == f"{ACCOUNT_LOGIN}/web"


async def test_injected_pr_is_also_queryable(pool, gh_run, gh_client, install_token):
    # Live PR is projected immediately, so REST reads see it (mirrors Slack).
    eid = await inject_github_event(pool, gh_run, kind="pull_request", repo="billing", handle="octocat")
    assert eid is not None
    r = await gh_client.get(
        f"/repos/{ACCOUNT_LOGIN}/billing/pulls", params={"state": "open"},
        headers={"Authorization": f"Bearer {install_token}"},
    )
    assert r.status_code == 200
    assert len(r.json()) >= 1
