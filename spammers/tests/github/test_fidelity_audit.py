"""Fidelity audit: the mock must be indistinguishable from real GitHub.

These assertions encode *real GitHub* behavior (documented response headers,
ETag/conditional requests, fixed-window rate limits, the issues-includes-PRs
rule, and the standard fields on each object), independent of what the mock
happened to return before.
"""
from __future__ import annotations

import time

import pytest

from spammers.tests.github.conftest import (
    ACCOUNT_LOGIN,
    ISSUE_NUM,
    PR_MERGED,
    PR_OPEN,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")

CORE = f"{ACCOUNT_LOGIN}/core"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ------------------------------ standard headers ------------------------------

async def test_standard_response_headers(gh_client, install_token):
    r = await gh_client.get(f"/repos/{CORE}", headers=_auth(install_token))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/json; charset=utf-8"
    assert r.headers["etag"]
    assert r.headers["x-github-media-type"] == "github.v3; format=json"
    assert r.headers["x-github-api-version"] == "2022-11-28"
    assert r.headers["x-github-request-id"]


# ------------------------------ rate limits ----------------------------------

async def test_rate_limit_headers_and_window(gh_client, install_token):
    r1 = await gh_client.get(f"/repos/{CORE}", headers=_auth(install_token))
    assert r1.headers["x-ratelimit-limit"] == "5000"
    reset = int(r1.headers["x-ratelimit-reset"])
    # Reset is a fixed hourly window boundary (UTC epoch seconds), not ~now.
    assert reset > int(time.time()) + 3000
    rem1 = int(r1.headers["x-ratelimit-remaining"])
    used1 = int(r1.headers["x-ratelimit-used"])
    assert rem1 + used1 == 5000

    r2 = await gh_client.get(f"/repos/{CORE}", headers=_auth(install_token))
    assert int(r2.headers["x-ratelimit-remaining"]) == rem1 - 1   # decrements per request
    assert int(r2.headers["x-ratelimit-reset"]) == reset          # same window


# --------------------------- conditional requests ----------------------------

async def test_etag_conditional_returns_304_and_does_not_count(gh_client, install_token):
    r1 = await gh_client.get(f"/repos/{CORE}", headers=_auth(install_token))
    etag = r1.headers["etag"]
    rem1 = int(r1.headers["x-ratelimit-remaining"])

    r2 = await gh_client.get(
        f"/repos/{CORE}", headers={**_auth(install_token), "If-None-Match": etag}
    )
    assert r2.status_code == 304
    # A 304 must not consume quota: remaining is unchanged from the prior request.
    assert int(r2.headers["x-ratelimit-remaining"]) == rem1


# --------------------------- issues include PRs -------------------------------

async def test_issues_list_includes_pull_requests(gh_client, install_token):
    r = await gh_client.get(
        f"/repos/{CORE}/issues", params={"state": "all"}, headers=_auth(install_token)
    )
    assert r.status_code == 200
    items = r.json()
    by_num = {i["number"]: i for i in items}
    # The plain issue and BOTH PRs appear (PRs are issues on GitHub).
    assert ISSUE_NUM in by_num
    assert PR_OPEN in by_num and PR_MERGED in by_num
    # The plain issue has no pull_request key; the PRs do.
    assert "pull_request" not in by_num[ISSUE_NUM]
    assert "pull_request" in by_num[PR_OPEN]
    assert by_num[PR_OPEN]["pull_request"]["html_url"].endswith(f"/pull/{PR_OPEN}")


# ----------------------------- object shapes ----------------------------------

async def test_user_object_shape(gh_client, install_token):
    pr = (await gh_client.get(f"/repos/{CORE}/pulls/{PR_OPEN}", headers=_auth(install_token))).json()
    u = pr["user"]
    for key in ("login", "id", "node_id", "avatar_url", "html_url", "type", "site_admin"):
        assert key in u, f"user missing {key}"
    assert isinstance(u["id"], int)
    assert u["type"] == "User"


async def test_pull_request_shape(gh_client, install_token):
    pr = (await gh_client.get(f"/repos/{CORE}/pulls/{PR_MERGED}", headers=_auth(install_token))).json()
    for key in ("id", "node_id", "number", "state", "title", "user", "head", "base",
                "merged", "merge_commit_sha", "html_url", "url", "issue_url",
                "author_association", "labels", "created_at"):
        assert key in pr, f"pull_request missing {key}"
    assert set(pr["head"]) >= {"ref", "sha", "label", "user"}
    assert pr["merged"] is True and pr["merge_commit_sha"]


async def test_issue_shape(gh_client, install_token):
    issue = (await gh_client.get(f"/repos/{CORE}/issues/{ISSUE_NUM}", headers=_auth(install_token))).json()
    for key in ("id", "node_id", "number", "state", "title", "user", "url",
                "repository_url", "comments_url", "html_url", "author_association"):
        assert key in issue, f"issue missing {key}"


async def test_repo_shape(gh_client, install_token):
    repo = (await gh_client.get(f"/repos/{CORE}", headers=_auth(install_token))).json()
    for key in ("id", "node_id", "name", "full_name", "private", "owner", "html_url",
                "url", "default_branch", "visibility"):
        assert key in repo, f"repo missing {key}"
    assert repo["owner"]["id"] and isinstance(repo["owner"]["id"], int)
    assert repo["visibility"] in ("public", "private")


async def test_commit_shape(gh_client, install_token):
    from spammers.tests.github.conftest import SHA1
    c = (await gh_client.get(f"/repos/{CORE}/commits/{SHA1}", headers=_auth(install_token))).json()
    assert c["sha"] == SHA1
    assert c["commit"]["message"]
    assert c["commit"]["author"]["email"]
    assert c["author"]["login"]               # nested user object
    assert "stats" in c and "total" in c["stats"]
