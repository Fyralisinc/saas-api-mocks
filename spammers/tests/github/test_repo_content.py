"""Repo content reads: pulls, issues, comments, commits, reviews, check-runs."""
from __future__ import annotations

import pytest

from spammers.tests.github.conftest import (
    ACCOUNT_LOGIN,
    ISSUE_NUM,
    PR_MERGED,
    PR_OPEN,
    SHA1,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")

REPO = f"{ACCOUNT_LOGIN}/core"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_list_pulls_default_open(gh_client, install_token):
    r = await gh_client.get(f"/repos/{REPO}/pulls", headers=_auth(install_token))
    assert r.status_code == 200
    nums = {pr["number"] for pr in r.json()}
    assert nums == {PR_OPEN}              # default state=open excludes the merged PR


async def test_list_pulls_all(gh_client, install_token):
    r = await gh_client.get(f"/repos/{REPO}/pulls", params={"state": "all"}, headers=_auth(install_token))
    nums = {pr["number"] for pr in r.json()}
    assert nums == {PR_OPEN, PR_MERGED}


async def test_get_pull_merged(gh_client, install_token):
    r = await gh_client.get(f"/repos/{REPO}/pulls/{PR_MERGED}", headers=_auth(install_token))
    assert r.status_code == 200
    pr = r.json()
    assert pr["number"] == PR_MERGED
    assert pr["merged"] is True
    assert pr["state"] == "closed"
    assert pr["head"]["sha"] == SHA1
    assert pr["user"]["login"] == "bob"


async def test_get_pull_404(gh_client, install_token):
    r = await gh_client.get(f"/repos/{REPO}/pulls/999", headers=_auth(install_token))
    assert r.status_code == 404


async def test_pull_reviews(gh_client, install_token):
    r = await gh_client.get(f"/repos/{REPO}/pulls/{PR_MERGED}/reviews", headers=_auth(install_token))
    assert r.status_code == 200
    reviews = r.json()
    assert len(reviews) == 1
    assert reviews[0]["state"] == "APPROVED"   # GitHub uppercases review state
    assert reviews[0]["user"]["login"] == "alice"


async def test_issue_and_comments(gh_client, install_token):
    r = await gh_client.get(f"/repos/{REPO}/issues/{ISSUE_NUM}", headers=_auth(install_token))
    assert r.status_code == 200
    issue = r.json()
    assert issue["number"] == ISSUE_NUM
    assert issue["comments"] == 1
    assert "pull_request" not in issue

    rc = await gh_client.get(f"/repos/{REPO}/issues/{ISSUE_NUM}/comments", headers=_auth(install_token))
    comments = rc.json()
    assert len(comments) == 1
    assert comments[0]["user"]["login"] == "bob"


async def test_pr_and_issue_share_number_space(gh_client, install_token):
    # #1, #2 are PRs; #3 is an issue — GitHub uses one sequence per repo.
    pr = await gh_client.get(f"/repos/{REPO}/pulls/{PR_OPEN}", headers=_auth(install_token))
    iss = await gh_client.get(f"/repos/{REPO}/issues/{ISSUE_NUM}", headers=_auth(install_token))
    assert pr.status_code == 200 and iss.status_code == 200
    # The issue number is not reused by a PR.
    pr_at_issue_num = await gh_client.get(f"/repos/{REPO}/pulls/{ISSUE_NUM}", headers=_auth(install_token))
    assert pr_at_issue_num.status_code == 404


async def test_commit_and_check_runs(gh_client, install_token):
    r = await gh_client.get(f"/repos/{REPO}/commits/{SHA1}", headers=_auth(install_token))
    assert r.status_code == 200
    assert r.json()["sha"] == SHA1
    assert r.json()["commit"]["author"]["name"] == "alice"

    cr = await gh_client.get(f"/repos/{REPO}/commits/{SHA1}/check-runs", headers=_auth(install_token))
    body = cr.json()
    assert body["total_count"] == 1
    assert body["check_runs"][0]["name"] == "build"
    assert body["check_runs"][0]["conclusion"] == "success"


async def test_list_commits(gh_client, install_token):
    r = await gh_client.get(f"/repos/{REPO}/commits", headers=_auth(install_token))
    assert r.status_code == 200
    assert any(c["sha"] == SHA1 for c in r.json())


async def test_unknown_repo_404(gh_client, install_token):
    r = await gh_client.get(f"/repos/{ACCOUNT_LOGIN}/ghost/pulls", headers=_auth(install_token))
    assert r.status_code == 404


async def test_requires_auth(gh_client):
    r = await gh_client.get(f"/repos/{REPO}/pulls")
    assert r.status_code == 401
