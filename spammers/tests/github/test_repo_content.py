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


async def test_list_pulls_honours_sort_direction(gh_client, install_token):
    # GitHub honours sort/direction; asc must be the exact reverse of desc so a
    # forward-scanning consumer (sort=updated&direction=asc) gets a stable order.
    base = {"state": "all", "sort": "created"}
    asc = await gh_client.get(
        f"/repos/{REPO}/pulls", params={**base, "direction": "asc"}, headers=_auth(install_token)
    )
    desc = await gh_client.get(
        f"/repos/{REPO}/pulls", params={**base, "direction": "desc"}, headers=_auth(install_token)
    )
    asc_nums = [pr["number"] for pr in asc.json()]
    desc_nums = [pr["number"] for pr in desc.json()]
    assert asc_nums == list(reversed(desc_nums))
    # created_at is non-decreasing under asc.
    created = [pr["created_at"] for pr in asc.json()]
    assert created == sorted(created)


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


async def test_dict_shaped_labels_are_normalized(pool, gh_run, gh_client, install_token):
    # The corpus stores labels as GitHub-style [{"name": ...}] objects, not bare
    # strings; the issue + label endpoints must normalize either shape. (Real
    # corpus data 500'd here before — the seed used string labels and hid it.)
    await pool.execute(
        """
        INSERT INTO app_github.issues
            (id, repo_pk, number, title, body, state, user_login, labels, created_at, updated_at)
        SELECT gen_random_uuid(), r.id, 9999, 'dict-labels', '', 'open', 'octocat',
               '[{"name":"bug"},{"name":"chore"}]'::jsonb, now(), now()
          FROM app_github.repositories r
          JOIN app_github.installations i ON i.id = r.installation_pk
          JOIN app_github.apps a ON a.id = i.app_pk
         WHERE a.run_id = $1 AND r.name = 'core'
        """,
        gh_run,
    )
    r = await gh_client.get(
        f"/repos/{ACCOUNT_LOGIN}/core/issues", params={"state": "all", "per_page": 100},
        headers=_auth(install_token),
    )
    assert r.status_code == 200
    issue = next(i for i in r.json() if i["number"] == 9999)
    assert {lbl["name"] for lbl in issue["labels"]} == {"bug", "chore"}
    assert all(len(lbl["color"]) == 6 for lbl in issue["labels"])  # colors still rendered

    lbls = await gh_client.get(f"/repos/{ACCOUNT_LOGIN}/core/labels", headers=_auth(install_token))
    assert {"bug", "chore"} <= {lbl["name"] for lbl in lbls.json()}


async def test_single_commit_has_files_but_list_does_not(gh_client, install_token):
    # Real GitHub returns per-file diffs on the single-commit GET only.
    one = await gh_client.get(f"/repos/{REPO}/commits/{SHA1}", headers=_auth(install_token))
    files = one.json()["files"]
    assert files and all({"filename", "status"} <= set(f) for f in files)
    assert files[0]["status"] in ("added", "modified", "removed", "renamed")

    lst = await gh_client.get(f"/repos/{REPO}/commits", headers=_auth(install_token))
    assert all("files" not in c for c in lst.json())

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
