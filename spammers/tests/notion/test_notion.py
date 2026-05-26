"""Notion mock — contract + behavior fidelity (API version 2022-06-28).

Encodes the backfill tree-walk + envelope shapes the consumer relies on:
search, database query, block children, comments, page hydration, users,
cursor pagination, the {object:error} envelope, and 429 + Retry-After.
"""
from __future__ import annotations

import pytest

from spammers.tests.notion.conftest import BOT_USER_ID, DB1_ID, PAGE_IDS

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_unauthed_401(notion_client):
    r = await notion_client.post("/v1/search", json={})
    assert r.status_code == 401
    body = r.json()
    assert body["object"] == "error" and body["code"] == "unauthorized"


async def test_search_by_object(notion_client, notion_auth):
    r = await notion_client.post("/v1/search", json={"filter": {"property": "object", "value": "database"}},
                                 headers=notion_auth)
    body = r.json()
    assert body["object"] == "list" and body["type"] == "page_or_database"
    assert len(body["results"]) == 2 and {x["object"] for x in body["results"]} == {"database"}

    r = await notion_client.post("/v1/search", json={"filter": {"property": "object", "value": "page"}},
                                 headers=notion_auth)
    body = r.json()
    assert len(body["results"]) == 3 and {x["object"] for x in body["results"]} == {"page"}
    assert body["has_more"] is False and body["next_cursor"] is None


async def test_search_reconcile_probe(notion_client, notion_auth):
    r = await notion_client.post("/v1/search",
                                 json={"sort": {"direction": "descending", "timestamp": "last_edited_time"},
                                       "page_size": 1}, headers=notion_auth)
    body = r.json()
    assert len(body["results"]) == 1


async def test_database_query(notion_client, notion_auth):
    r = await notion_client.post(f"/v1/databases/{DB1_ID}/query", json={}, headers=notion_auth)
    body = r.json()
    assert body["type"] == "page" and len(body["results"]) == 3
    page = body["results"][0]
    assert page["object"] == "page" and page["parent"]["type"] == "database_id"
    assert page["properties"]["Name"]["type"] == "title"
    assert page["properties"]["Name"]["title"][0]["plain_text"].startswith("Doc")


async def test_database_query_pagination(notion_client, notion_auth):
    r = await notion_client.post(f"/v1/databases/{DB1_ID}/query", json={"page_size": 2}, headers=notion_auth)
    body = r.json()
    assert len(body["results"]) == 2 and body["has_more"] is True and body["next_cursor"]
    r2 = await notion_client.post(f"/v1/databases/{DB1_ID}/query",
                                  json={"page_size": 2, "start_cursor": body["next_cursor"]}, headers=notion_auth)
    assert len(r2.json()["results"]) == 1


async def test_block_children(notion_client, notion_auth):
    r = await notion_client.get(f"/v1/blocks/{PAGE_IDS[0]}/children", headers=notion_auth)
    body = r.json()
    assert body["type"] == "block" and len(body["results"]) == 2
    blk = body["results"][0]
    assert blk["object"] == "block" and blk["parent"]["page_id"] == PAGE_IDS[0]
    assert blk["type"] in blk  # the type-specific key is present


async def test_comments(notion_client, notion_auth):
    r = await notion_client.get(f"/v1/comments?block_id={PAGE_IDS[0]}", headers=notion_auth)
    body = r.json()
    assert body["type"] == "comment" and len(body["results"]) == 1
    assert body["results"][0]["parent"]["page_id"] == PAGE_IDS[0]


async def test_comments_requires_block_id(notion_client, notion_auth):
    r = await notion_client.get("/v1/comments", headers=notion_auth)
    assert r.status_code == 400 and r.json()["code"] == "validation_error"


async def test_get_page_and_404(notion_client, notion_auth):
    r = await notion_client.get(f"/v1/pages/{PAGE_IDS[0]}", headers=notion_auth)
    assert r.status_code == 200 and r.json()["object"] == "page"
    r = await notion_client.get("/v1/pages/does-not-exist", headers=notion_auth)
    assert r.status_code == 404 and r.json()["code"] == "object_not_found"


async def test_users(notion_client, notion_auth):
    r = await notion_client.get("/v1/users/me", headers=notion_auth)
    assert r.status_code == 200 and r.json()["type"] == "bot"

    r = await notion_client.get("/v1/users", headers=notion_auth)
    body = r.json()
    assert body["object"] == "list" and body["type"] == "user"
    # bot + 2 people
    assert len(body["results"]) == 3
    assert body["results"][0]["type"] == "bot" and body["results"][0]["id"] == BOT_USER_ID
    assert {u["type"] for u in body["results"][1:]} == {"person"}


async def test_unknown_path_returns_notion_envelope(notion_client, notion_auth):
    r = await notion_client.get("/v1/bogus", headers=notion_auth)
    assert r.status_code == 404
    body = r.json()
    assert body["object"] == "error" and body["code"] == "object_not_found"
    assert "detail" not in body  # not FastAPI's default shape


async def test_rate_limit_429(notion_client, notion_auth):
    saw = None
    for _ in range(30):  # cap 15, refill 3/s — a fast burst trips it
        r = await notion_client.post("/v1/search", json={}, headers=notion_auth)
        if r.status_code == 429:
            saw = r
            break
    assert saw is not None, "expected a 429 under burst"
    assert saw.json()["code"] == "rate_limited"
    assert int(saw.headers["Retry-After"]) >= 1
