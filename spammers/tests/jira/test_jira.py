"""Jira mock — contract + behavior fidelity (Jira Cloud REST v3).

Encodes the real behavior the consumer relies on: HTTP Basic auth, the modern
token-paginated ``/search/jql`` (no startAt/total; isLast + nextPageToken),
inline ``expand=changelog`` histories + ``fields.comment`` comments, JQL
``updated >=`` minute-precision filtering, ``project/search`` (startAt paging),
``approximate-count``, ``myself``, and 429 + Retry-After.
"""
from __future__ import annotations

import pytest

from spammers.tests.jira.conftest import ACCOUNT_EMAIL, BASE_URL, PROJECT_KEY, basic_header

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ---- auth -----------------------------------------------------------------

async def test_unauthed_401(jira_client):
    r = await jira_client.get("/rest/api/3/myself")
    assert r.status_code == 401 and "errorMessages" in r.json()


async def test_bad_token_401(jira_client):
    r = await jira_client.get("/rest/api/3/myself", headers=basic_header(token="wrong"))
    assert r.status_code == 401


async def test_myself(jira_client, jira_auth):
    r = await jira_client.get("/rest/api/3/myself", headers=jira_auth)
    assert r.status_code == 200 and r.json()["emailAddress"] == ACCOUNT_EMAIL


# ---- project search -------------------------------------------------------

async def test_project_search(jira_client, jira_auth):
    r = await jira_client.get("/rest/api/3/project/search", headers=jira_auth)
    body = r.json()
    assert body["total"] == 1 and body["isLast"] is True
    assert body["values"][0]["key"] == PROJECT_KEY
    assert body["startAt"] == 0


# ---- search/jql backfill --------------------------------------------------

async def test_search_jql_backfill(jira_client, jira_auth):
    body = {"jql": f'project = "{PROJECT_KEY}" ORDER BY updated ASC',
            "maxResults": 50, "fields": ["summary", "status", "comment", "updated"],
            "expand": "changelog"}
    r = await jira_client.post("/rest/api/3/search/jql", json=body, headers=jira_auth)
    resp = r.json()
    assert resp["isLast"] is True and "nextPageToken" not in resp
    issues = resp["issues"]
    assert len(issues) == 3
    # ORDER BY updated ASC -> ENG-1 (offset 0h) first.
    assert issues[0]["key"] == "ENG-1"
    f = issues[0]["fields"]
    assert f["status"]["name"] == "Done"
    assert f["status"]["statusCategory"]["key"] == "done"
    # comments inline via fields.comment
    assert f["comment"]["total"] == 1
    assert f["comment"]["comments"][0]["body"]["type"] == "doc"  # ADF
    # changelog inline via expand
    histories = issues[0]["changelog"]["histories"]
    assert len(histories) == 1
    item = histories[0]["items"][0]
    assert item["field"] == "status" and item["toString"] == "Done"


async def test_search_jql_pagination(jira_client, jira_auth):
    body = {"jql": f'project = "{PROJECT_KEY}" ORDER BY updated ASC',
            "maxResults": 2, "fields": ["summary"]}
    r = await jira_client.post("/rest/api/3/search/jql", json=body, headers=jira_auth)
    resp = r.json()
    assert len(resp["issues"]) == 2 and resp["isLast"] is False and resp["nextPageToken"]
    r2 = await jira_client.post(
        "/rest/api/3/search/jql",
        json={**body, "nextPageToken": resp["nextPageToken"]}, headers=jira_auth)
    resp2 = r2.json()
    assert len(resp2["issues"]) == 1 and resp2["isLast"] is True


async def test_search_jql_updated_incremental(jira_client, jira_auth):
    # updated >= _T0+2h (minute literal) -> ENG-2 (2h) and ENG-3 (4h); ENG-1 (0h) excluded.
    body = {"jql": f'project = "{PROJECT_KEY}" AND updated >= "2026/01/10 11:00" ORDER BY updated ASC',
            "fields": ["summary", "updated"]}
    r = await jira_client.post("/rest/api/3/search/jql", json=body, headers=jira_auth)
    keys = {i["key"] for i in r.json()["issues"]}
    assert keys == {"ENG-2", "ENG-3"}


async def test_approximate_count(jira_client, jira_auth):
    r = await jira_client.post(
        "/rest/api/3/search/approximate-count",
        json={"jql": f'project = "{PROJECT_KEY}"'}, headers=jira_auth)
    assert r.json()["count"] == 3
    r = await jira_client.post(
        "/rest/api/3/search/approximate-count",
        json={"jql": f'project = "{PROJECT_KEY}" AND updated >= "2026/01/10 11:00"'}, headers=jira_auth)
    assert r.json()["count"] == 2


async def test_search_jql_get(jira_client, jira_auth):
    # Real Jira supports GET on /search/jql for small queries (params in the query string).
    r = await jira_client.get(
        "/rest/api/3/search/jql",
        params={"jql": f'project = "{PROJECT_KEY}" ORDER BY updated ASC',
                "maxResults": "50", "fields": "summary,status,comment", "expand": "changelog"},
        headers=jira_auth)
    assert r.status_code == 200
    resp = r.json()
    assert len(resp["issues"]) == 3 and resp["isLast"] is True
    assert resp["issues"][0]["fields"]["comment"]["total"] == 1
    assert len(resp["issues"][0]["changelog"]["histories"]) == 1


# ---- field selection (the new /search/jql contract) -----------------------

async def test_search_jql_default_fields_id_only(jira_client, jira_auth):
    # Real /search/jql default (no `fields`) returns IDs ONLY — `fields` == {} —
    # unlike the old /search whose default was *navigable.
    body = {"jql": f'project = "{PROJECT_KEY}" ORDER BY updated ASC', "maxResults": 50}
    r = await jira_client.post("/rest/api/3/search/jql", json=body, headers=jira_auth)
    issues = r.json()["issues"]
    assert len(issues) == 3
    for i in issues:
        assert {"id", "key", "self", "fields"} <= set(i)
        assert i["fields"] == {}


async def test_search_jql_field_projection(jira_client, jira_auth):
    # An explicit `fields` returns ONLY those keys — not the full issue.
    body = {"jql": f'project = "{PROJECT_KEY}" ORDER BY updated ASC',
            "fields": ["summary", "status"]}
    r = await jira_client.post("/rest/api/3/search/jql", json=body, headers=jira_auth)
    f = r.json()["issues"][0]["fields"]
    assert set(f) == {"summary", "status"}
    assert "comment" not in f and "reporter" not in f and "description" not in f


async def test_search_jql_navigable_vs_all(jira_client, jira_auth):
    # *navigable returns the navigable fields but NOT comment; *all includes it.
    nav = await jira_client.post(
        "/rest/api/3/search/jql",
        json={"jql": f'project = "{PROJECT_KEY}" ORDER BY updated ASC',
              "fields": ["*navigable"]}, headers=jira_auth)
    f = nav.json()["issues"][0]["fields"]
    assert "summary" in f and "reporter" in f and "status" in f
    assert "comment" not in f
    allf = await jira_client.post(
        "/rest/api/3/search/jql",
        json={"jql": f'project = "{PROJECT_KEY}" ORDER BY updated ASC',
              "fields": ["*all"]}, headers=jira_auth)
    # ENG-1 (first, updated ASC) carries one comment.
    assert allf.json()["issues"][0]["fields"]["comment"]["total"] == 1


async def test_status_category_full_shape(jira_client, jira_auth):
    body = {"jql": f'project = "{PROJECT_KEY}" ORDER BY updated ASC', "fields": ["status"]}
    r = await jira_client.post("/rest/api/3/search/jql", json=body, headers=jira_auth)
    st = r.json()["issues"][0]["fields"]["status"]  # ENG-1 -> Done
    assert st["name"] == "Done"
    assert st["self"].startswith(BASE_URL)
    cat = st["statusCategory"]
    assert cat["id"] == 3 and cat["key"] == "done"
    assert cat["colorName"] == "green"
    assert cat["self"].endswith("/statuscategory/3")


async def test_user_object_self_and_avatars(jira_client, jira_auth):
    body = {"jql": f'project = "{PROJECT_KEY}" ORDER BY updated ASC', "fields": ["reporter"]}
    r = await jira_client.post("/rest/api/3/search/jql", json=body, headers=jira_auth)
    rep = r.json()["issues"][0]["fields"]["reporter"]
    # Real Jira: User.self is the per-site account URL, never a placeholder host.
    assert rep["self"] == f"{BASE_URL}/rest/api/3/user?accountId={rep['accountId']}"
    assert "https://mock" not in rep["self"]
    assert set(rep["avatarUrls"]) == {"16x16", "24x24", "32x32", "48x48"}


async def test_error_envelope_not_fastapi_default(jira_client, jira_auth):
    # Unknown path -> Jira envelope, not FastAPI's {"detail":…}.
    r = await jira_client.get("/rest/api/3/bogus", headers=jira_auth)
    assert r.status_code == 404
    body = r.json()
    assert "errorMessages" in body and "errors" in body and "detail" not in body


async def test_unknown_project_empty(jira_client, jira_auth):
    r = await jira_client.post(
        "/rest/api/3/search/jql",
        json={"jql": 'project = "NOPE" ORDER BY updated ASC'}, headers=jira_auth)
    assert r.json() == {"issues": [], "isLast": True}


# ---- rate limiting --------------------------------------------------------

async def test_rate_limit_429(jira_client, jira_auth):
    from spammers.jira.ratelimit import _RL, _CAP, _REFILL
    await _RL.take(f"jira:{ACCOUNT_EMAIL}", capacity=_CAP, refill_per_sec=_REFILL, cost=_CAP)
    r = await jira_client.get("/rest/api/3/myself", headers=jira_auth)
    assert r.status_code == 429
    assert "errorMessages" in r.json()
    assert int(r.headers["Retry-After"]) >= 1
