"""Installation-token REST: /installation/repositories and /repos/{owner}/{repo}."""
from __future__ import annotations

import pytest

from spammers.tests.github.conftest import ACCOUNT_LOGIN, REPOS

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_installation_repositories(gh_client, install_token):
    r = await gh_client.get("/installation/repositories", headers=_auth(install_token))
    assert r.status_code == 200
    body = r.json()
    assert body["total_count"] == len(REPOS)
    names = {repo["name"] for repo in body["repositories"]}
    assert names == {name for _o, name, _id in REPOS}
    repo = body["repositories"][0]
    assert repo["full_name"] == f"{repo['owner']['login']}/{repo['name']}"
    assert repo["owner"]["login"] == ACCOUNT_LOGIN
    assert isinstance(repo["id"], int)


async def test_repositories_pagination_link_header(gh_client, install_token):
    # First page (of 3): GitHub emits only next + last, never prev/first.
    r = await gh_client.get(
        "/installation/repositories", params={"per_page": 1, "page": 1},
        headers=_auth(install_token),
    )
    assert r.status_code == 200
    assert len(r.json()["repositories"]) == 1
    link = r.headers.get("Link")
    assert link and 'rel="next"' in link and 'rel="last"' in link
    assert 'rel="prev"' not in link and 'rel="first"' not in link


async def test_pagination_link_header_middle_and_last_pages(gh_client, install_token):
    # Middle page (2 of 3): all four relations present.
    mid = await gh_client.get(
        "/installation/repositories", params={"per_page": 1, "page": 2},
        headers=_auth(install_token),
    )
    link = mid.headers.get("Link")
    assert link
    for rel in ('rel="prev"', 'rel="next"', 'rel="last"', 'rel="first"'):
        assert rel in link

    # Last page (3 of 3): GitHub emits only prev + first, never next/last.
    last = await gh_client.get(
        "/installation/repositories", params={"per_page": 1, "page": 3},
        headers=_auth(install_token),
    )
    link = last.headers.get("Link")
    assert link and 'rel="prev"' in link and 'rel="first"' in link
    assert 'rel="next"' not in link and 'rel="last"' not in link


async def test_rate_limit_headers_present(gh_client, install_token):
    r = await gh_client.get("/installation/repositories", headers=_auth(install_token))
    assert r.headers["X-RateLimit-Limit"] == "5000"
    assert int(r.headers["X-RateLimit-Remaining"]) <= 5000
    assert "X-RateLimit-Reset" in r.headers
    assert r.headers["X-RateLimit-Resource"] == "core"


async def test_get_repo(gh_client, install_token):
    r = await gh_client.get(f"/repos/{ACCOUNT_LOGIN}/core", headers=_auth(install_token))
    assert r.status_code == 200
    body = r.json()
    assert body["full_name"] == f"{ACCOUNT_LOGIN}/core"
    assert body["default_branch"] == "main"


async def test_get_repo_not_found(gh_client, install_token):
    r = await gh_client.get(f"/repos/{ACCOUNT_LOGIN}/nope", headers=_auth(install_token))
    assert r.status_code == 404
    assert r.json()["message"] == "Not Found"


async def test_rest_without_token_is_401(gh_client):
    r = await gh_client.get("/installation/repositories")
    assert r.status_code == 401
    assert r.json()["message"] == "Bad credentials"


async def test_rest_with_garbage_token_is_401(gh_client):
    r = await gh_client.get("/installation/repositories", headers=_auth("ghs_notreal"))
    assert r.status_code == 401


async def test_content_type_charset(gh_client, install_token):
    r = await gh_client.get("/installation/repositories", headers=_auth(install_token))
    assert r.headers["content-type"] == "application/json; charset=utf-8"
