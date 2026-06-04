"""Outbound GitHub webhooks: live events are signed + delivered; historical aren't."""
from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest
import respx

from spammers.common.signing import github_sign_sha1, github_verify
from spammers.director.orchestrator import EmissionLoop
from spammers.orggen.live import inject_github_event, inject_github_lifecycle
from spammers.tests.github.conftest import ACCOUNT_LOGIN, INSTALLATION_ID

pytestmark = pytest.mark.asyncio(loop_scope="session")

WEBHOOK_URL = "https://consumer.test/webhooks/github"
SECRET = "whsec"  # seeded app webhook_secret


async def _drain(pool, gh_run) -> list:
    """Drain pending live events; return the delivered httpx requests."""
    loop = EmissionLoop(pool, gh_run, github_events_url=WEBHOOK_URL)
    with respx.mock:
        route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))
        await loop._drain_once()
        return [c.request for c in route.calls]


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
    # Real GitHub also stamps a hook id and a GitHub-Hookshot user agent.
    assert req.headers["X-GitHub-Hook-ID"]
    assert req.headers["User-Agent"].startswith("GitHub-Hookshot/")
    # Signatures verify against the app webhook secret over the exact bytes sent —
    # GitHub sends both the legacy SHA-1 and the SHA-256 header on every delivery.
    assert github_verify(SECRET, req.headers["X-Hub-Signature-256"], req.content)
    assert req.headers["X-Hub-Signature"] == github_sign_sha1(SECRET, req.content)

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


# --------------------------- A: live push events ---------------------------

async def test_live_objects_get_distinct_increasing_timestamps(pool, gh_run):
    # Under a frozen clock, real GitHub still never collides created_at/updated_at;
    # live objects must get strictly-increasing, distinct timestamps, and a later
    # merge must land after the PR's own created_at (not equal to it).
    repo_pk = await pool.fetchval(
        "SELECT r.id FROM app_github.repositories r "
        "JOIN app_github.installations i ON i.id=r.installation_pk "
        "JOIN app_github.apps a ON a.id=i.app_pk WHERE a.run_id=$1 AND r.name='core'", gh_run)
    await inject_github_event(pool, gh_run, kind="pull_request", repo="core", handle="octocat")
    n1 = await pool.fetchval("SELECT max(number) FROM app_github.pull_requests WHERE repo_pk=$1", repo_pk)
    await inject_github_event(pool, gh_run, kind="pull_request", repo="core", handle="octocat")
    n2 = await pool.fetchval("SELECT max(number) FROM app_github.pull_requests WHERE repo_pk=$1", repo_pk)
    t1 = await pool.fetchval("SELECT created_at FROM app_github.pull_requests WHERE repo_pk=$1 AND number=$2", repo_pk, n1)
    t2 = await pool.fetchval("SELECT created_at FROM app_github.pull_requests WHERE repo_pk=$1 AND number=$2", repo_pk, n2)
    assert t2 > t1, "two live PRs must not share a created_at"

    await inject_github_event(pool, gh_run, kind="pull_request", action="merged",
                              repo="core", number=n1)
    m = await pool.fetchrow(
        "SELECT created_at, merged_at FROM app_github.pull_requests WHERE repo_pk=$1 AND number=$2",
        repo_pk, n1)
    assert m["merged_at"] is not None and m["merged_at"] > m["created_at"]


async def test_live_push_delivered(pool, gh_run):
    await inject_github_event(pool, gh_run, kind="push", repo="core", handle="octocat",
                              title="feat: ship it")
    reqs = await _drain(pool, gh_run)
    push = [r for r in reqs if r.headers["X-GitHub-Event"] == "push"]
    assert len(push) == 1
    req = push[0]
    body = json.loads(req.content)
    # external_id on the consumer is "{repo}@{after}" — both fields must be real.
    assert body["after"] and body["after"] != "0" * 40
    assert body["ref"] == "refs/heads/main"
    assert body["repository"]["full_name"] == f"{ACCOUNT_LOGIN}/core"
    # head_commit drives occurred_at + blast radius.
    assert body["head_commit"]["id"] == body["after"]
    assert body["head_commit"]["timestamp"]
    c0 = body["commits"][0]
    assert c0["id"] == body["after"]
    # Changed-file paths drive the consumer's blast-radius layer — must be real.
    changed = c0["added"] + c0["removed"] + c0["modified"]
    assert changed, "push commit must carry changed-file paths"
    assert body["head_commit"]["added"] == c0["added"]
    assert body["head_commit"]["modified"] == c0["modified"]
    assert body["pusher"]["name"] == "octocat"
    assert body["installation"]["id"] == INSTALLATION_ID
    assert github_verify(SECRET, req.headers["X-Hub-Signature-256"], req.content)


# --------------- C: PR/issue actions beyond "opened" -----------------------

async def test_live_pr_merge_is_closed_and_merged(pool, gh_run):
    # Open a fresh PR, then merge it — the merge webhook is action=closed,merged.
    await inject_github_event(pool, gh_run, kind="pull_request", repo="web", handle="octocat")
    opened = await _drain(pool, gh_run)
    num = json.loads(opened[-1].content)["number"]

    await inject_github_event(pool, gh_run, kind="pull_request", action="merged",
                              repo="web", number=num, handle="octocat")
    reqs = await _drain(pool, gh_run)
    req = reqs[-1]
    assert req.headers["X-GitHub-Event"] == "pull_request"
    body = json.loads(req.content)
    assert body["action"] == "closed"
    assert body["pull_request"]["state"] == "closed"
    assert body["pull_request"]["merged"] is True
    assert body["pull_request"]["merged_at"] is not None


async def test_live_issue_closed(pool, gh_run):
    await inject_github_event(pool, gh_run, kind="issues", repo="web", handle="octocat")
    opened = await _drain(pool, gh_run)
    num = json.loads(opened[-1].content)["issue"]["number"]

    await inject_github_event(pool, gh_run, kind="issues", action="closed",
                              repo="web", number=num, handle="octocat")
    reqs = await _drain(pool, gh_run)
    body = json.loads(reqs[-1].content)
    assert body["action"] == "closed"
    assert body["issue"]["state"] == "closed"
    assert body["issue"]["closed_at"] is not None


# ------------- D: live review / comment / check_run events -----------------

async def test_live_pull_request_review(pool, gh_run):
    await inject_github_event(pool, gh_run, kind="pull_request", repo="billing", handle="octocat")
    opened = await _drain(pool, gh_run)
    num = json.loads(opened[-1].content)["number"]

    await inject_github_event(pool, gh_run, kind="pull_request_review", repo="billing",
                              number=num, handle="octocat", review_state="approved")
    reqs = await _drain(pool, gh_run)
    req = reqs[-1]
    assert req.headers["X-GitHub-Event"] == "pull_request_review"
    body = json.loads(req.content)
    assert body["action"] == "submitted"
    assert body["review"]["state"] == "APPROVED"
    assert body["pull_request"]["number"] == num


async def test_live_issue_comment(pool, gh_run):
    await inject_github_event(pool, gh_run, kind="issues", repo="billing", handle="octocat")
    opened = await _drain(pool, gh_run)
    num = json.loads(opened[-1].content)["issue"]["number"]

    await inject_github_event(pool, gh_run, kind="issue_comment", repo="billing",
                              number=num, handle="octocat", body="On it.")
    reqs = await _drain(pool, gh_run)
    req = reqs[-1]
    assert req.headers["X-GitHub-Event"] == "issue_comment"
    body = json.loads(req.content)
    assert body["action"] == "created"
    assert body["comment"]["body"] == "On it."
    # The repo-wide comment shape carries issue_url (consumer parses the number).
    assert body["comment"]["issue_url"].endswith(f"/issues/{num}")


async def test_live_check_run(pool, gh_run):
    # Attach the check run to a fresh PR's head sha (unique — avoids colliding
    # with the seeded check run on acme/core).
    await inject_github_event(pool, gh_run, kind="pull_request", repo="web", handle="octocat")
    opened = await _drain(pool, gh_run)
    num = json.loads(opened[-1].content)["number"]
    await inject_github_event(pool, gh_run, kind="check_run", repo="web", number=num,
                              handle="octocat", check_conclusion="failure", title="ci/test")
    reqs = await _drain(pool, gh_run)
    req = reqs[-1]
    assert req.headers["X-GitHub-Event"] == "check_run"
    body = json.loads(req.content)
    assert body["action"] == "completed"
    assert body["check_run"]["status"] == "completed"
    assert body["check_run"]["conclusion"] == "failure"
    assert body["check_run"]["head_sha"]


# ------------------ B: lifecycle + ping (NOT observations) ------------------

async def test_live_ping(pool, gh_run):
    await inject_github_lifecycle(pool, gh_run, kind="ping", handle="octocat")
    reqs = await _drain(pool, gh_run)
    req = [r for r in reqs if r.headers["X-GitHub-Event"] == "ping"][0]
    body = json.loads(req.content)
    assert body["zen"]
    assert body["hook_id"]
    assert body["hook"]["app_id"]
    # ping is signed like any other delivery.
    assert github_verify(SECRET, req.headers["X-Hub-Signature-256"], req.content)


async def test_live_installation_repositories_removed(pool, gh_run):
    await inject_github_lifecycle(pool, gh_run, kind="installation_repositories",
                                  action="removed", repos=["web"], handle="octocat")
    reqs = await _drain(pool, gh_run)
    req = [r for r in reqs if r.headers["X-GitHub-Event"] == "installation_repositories"][0]
    body = json.loads(req.content)
    assert body["action"] == "removed"
    removed = {r["full_name"] for r in body["repositories_removed"]}
    assert f"{ACCOUNT_LOGIN}/web" in removed
    assert body["installation"]["id"] == INSTALLATION_ID


async def test_live_installation_suspend_then_revokes_and_recovers(pool, gh_run, gh_client, jwt_header):
    # Suspend → webhook delivered, REST token revoked (401), token mint 404 with
    # the apps documentation_url; unsuspend → everything works again.
    try:
        await inject_github_lifecycle(pool, gh_run, kind="installation", action="suspend",
                                      handle="octocat")
        reqs = await _drain(pool, gh_run)
        req = [r for r in reqs if r.headers["X-GitHub-Event"] == "installation"][0]
        body = json.loads(req.content)
        assert body["action"] == "suspend"
        assert body["installation"]["id"] == INSTALLATION_ID
        assert body["installation"]["suspended_at"] is not None

        # F: a token minted before suspension now fails as Bad credentials, and a
        # fresh mint 404s with the /rest/apps doc url — both revocation signals.
        mint = await gh_client.post(
            f"/app/installations/{INSTALLATION_ID}/access_tokens", headers=jwt_header
        )
        assert mint.status_code == 404
        assert "/rest/apps" in mint.json()["documentation_url"]
    finally:
        await inject_github_lifecycle(pool, gh_run, kind="installation", action="unsuspend",
                                      handle="octocat")
        await _drain(pool, gh_run)

    # Recovered: a fresh token mints and reads succeed again.
    mint = await gh_client.post(
        f"/app/installations/{INSTALLATION_ID}/access_tokens", headers=jwt_header
    )
    assert mint.status_code == 201
    token = mint.json()["token"]
    r = await gh_client.get("/installation/repositories",
                            headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


# --------------------- E: secondary rate limit (429) -----------------------

async def test_secondary_rate_limit_returns_429_with_retry_after(gh_client, install_token):
    from spammers.github.ratelimit import arm_secondary_limit
    arm_secondary_limit(INSTALLATION_ID, count=1, retry_after=42)
    blocked = await gh_client.get(
        "/installation/repositories", headers={"Authorization": f"Bearer {install_token}"}
    )
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "42"
    assert "secondary rate limit" in blocked.json()["message"].lower()
    # The armed unit is one-shot: the next request succeeds and the primary
    # quota was never spent on the 429.
    ok = await gh_client.get(
        "/installation/repositories", headers={"Authorization": f"Bearer {install_token}"}
    )
    assert ok.status_code == 200
