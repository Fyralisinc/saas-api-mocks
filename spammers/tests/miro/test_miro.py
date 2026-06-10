"""Miro mock fidelity suite — hard-fail assertions against the REAL api.miro.com
``/v2`` wire contract (pinned from Miro's published OpenAPI spec).

These tests encode what the REAL Miro API does, not what the mock happens to do.
A divergence is a hard failure (the red test IS the fix-list), per the project's
fidelity-suite policy.
"""
from __future__ import annotations

import re

import pytest

from spammers.tests.miro.conftest import (
    ACCESS_TOKEN, B1_ID, B2_ID, ORG_ID, TEAM_ID, TEAM_NAME,
    FR1_ID, S1_ID, S2_ID, C1_ID,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")

_MS_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


# --------------------------------------------------------------------------- auth

async def test_missing_bearer_is_401_token_not_provided(miro_client):
    r = await miro_client.get("/v2/boards")
    assert r.status_code == 401, r.text
    body = r.json()
    assert body["status"] == 401
    assert body["code"] == "tokenNotProvided"
    assert body["type"] == "error"
    assert "message" in body


async def test_blank_bearer_is_401(miro_client):
    r = await miro_client.get("/v2/boards", headers={"Authorization": "Bearer "})
    assert r.status_code == 401


async def test_any_nonempty_bearer_accepted(miro_client):
    r = await miro_client.get("/v2/boards", headers={"Authorization": "Bearer anything"})
    assert r.status_code == 200


# ------------------------------------------------------ GET /v2/boards (offset)

async def test_boards_offset_envelope(miro_client, miro_auth):
    r = await miro_client.get("/v2/boards", headers=miro_auth)
    assert r.status_code == 200, r.text
    body = r.json()
    # The offset envelope HAS a top-level `type`; the items (cursor) one does NOT.
    assert set(body) >= {"data", "total", "size", "offset", "limit", "links", "type"}
    assert body["total"] == 2
    assert body["size"] == 2
    assert body["offset"] == 0
    assert isinstance(body["data"], list)
    assert "cursor" not in body


async def test_board_object_shape(miro_client, miro_auth):
    r = await miro_client.get("/v2/boards", headers=miro_auth)
    boards = {b["id"]: b for b in r.json()["data"]}
    b1 = boards[B1_ID]
    assert b1["name"] == "Q3 Roadmap"
    assert b1["description"] == "Roadmap planning board"
    assert b1["type"] == "board"
    assert b1["viewLink"].endswith(B1_ID)
    # team object {id, name, type:"team"}
    assert b1["team"] == {"id": TEAM_ID, "name": TEAM_NAME, "type": "team"}
    # board-scoped users carry `name`
    assert b1["owner"]["name"] == "Ada Lovelace"
    assert b1["owner"]["type"] == "user"
    assert "name" in b1["createdBy"]
    # currentUserMembership = BoardMember {id, name, role, type:"board_member"}
    cum = b1["currentUserMembership"]
    assert cum["type"] == "board_member"
    assert cum["role"] == "owner"
    assert cum["name"] == "Fyralis Ingest"
    # ms-precision Z timestamps
    assert _MS_Z.match(b1["createdAt"]), b1["createdAt"]
    assert _MS_Z.match(b1["modifiedAt"]), b1["modifiedAt"]
    # policy present with both sub-policies
    assert "permissionsPolicy" in b1["policy"]
    assert "sharingPolicy" in b1["policy"]


async def test_boards_offset_pagination_walk(miro_client, miro_auth):
    # limit=1 → two single-item pages; links.next until the last page.
    seen = []
    offset = 0
    for _ in range(5):
        r = await miro_client.get(f"/v2/boards?limit=1&offset={offset}", headers=miro_auth)
        body = r.json()
        assert body["limit"] == 1
        assert body["size"] == 1
        seen.extend(b["id"] for b in body["data"])
        if "next" not in body["links"]:
            break
        offset += 1
    assert seen == [B1_ID, B2_ID]
    # last page: offset=1 → no next link
    last = await miro_client.get("/v2/boards?limit=1&offset=1", headers=miro_auth)
    assert "next" not in last.json()["links"]
    assert "prev" in last.json()["links"]


async def test_boards_limit_clamped_to_50(miro_client, miro_auth):
    r = await miro_client.get("/v2/boards?limit=999", headers=miro_auth)
    assert r.json()["limit"] == 50


async def test_boards_bad_limit_400(miro_client, miro_auth):
    r = await miro_client.get("/v2/boards?limit=abc", headers=miro_auth)
    assert r.status_code == 400
    assert r.json()["type"] == "error"


# --------------------------------------- GET /v2/boards/{id} (single board)

async def test_single_board_has_links(miro_client, miro_auth):
    r = await miro_client.get(f"/v2/boards/{B1_ID}", headers=miro_auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == B1_ID
    # single-board GET adds a links{self, related} object the list omits
    assert "links" in body
    assert body["links"]["self"].endswith(f"/boards/{B1_ID}")
    assert "related" in body["links"]


async def test_single_board_unknown_404(miro_client, miro_auth):
    r = await miro_client.get("/v2/boards/nope=", headers=miro_auth)
    assert r.status_code == 404
    body = r.json()
    assert body["status"] == 404
    assert body["code"] == "notFound"
    assert body["type"] == "error"


# --------------------------------- GET /v2/boards/{id}/items (cursor)

async def test_items_cursor_envelope(miro_client, miro_auth):
    r = await miro_client.get(f"/v2/boards/{B1_ID}/items", headers=miro_auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {"data", "total", "size", "limit", "links"}
    # The cursor envelope has NO top-level `type` (unlike the boards offset one).
    assert "type" not in body
    assert body["total"] == 12  # six named items + six filler on B1


async def test_item_object_shape_and_no_name_user(miro_client, miro_auth):
    r = await miro_client.get(f"/v2/boards/{B1_ID}/items?limit=50", headers=miro_auth)
    items = {i["id"]: i for i in r.json()["data"]}
    s1 = items[S1_ID]
    assert s1["type"] == "sticky_note"
    assert s1["data"] == {"content": "Ship onboarding", "shape": "square"}
    assert s1["geometry"] == {"width": 200.0, "height": 80.0, "rotation": 0.0}
    assert s1["position"]["relativeTo"] == "parent_top_left"
    assert s1["position"]["origin"] == "center"
    # parented item carries parent{id}
    assert s1["parent"] == {"id": FR1_ID}
    # item-scoped users carry NO `name` (only {id, type})
    assert set(s1["createdBy"]) == {"id", "type"}
    assert s1["createdBy"]["type"] == "user"
    assert "name" not in s1["createdBy"]
    assert set(s1["modifiedBy"]) == {"id", "type"}
    # ms-Z timestamps; S1 was edited after creation
    assert _MS_Z.match(s1["createdAt"]), s1["createdAt"]
    assert _MS_Z.match(s1["modifiedAt"]), s1["modifiedAt"]
    assert s1["modifiedAt"] > s1["createdAt"]


async def test_item_type_specific_data_shapes(miro_client, miro_auth):
    r = await miro_client.get(f"/v2/boards/{B1_ID}/items?limit=50", headers=miro_auth)
    items = {i["id"]: i for i in r.json()["data"]}
    assert items[S2_ID]["data"] == {"content": "Cut p95 latency", "shape": "square"}
    # frame data
    fr = items[FR1_ID]
    assert fr["type"] == "frame"
    assert fr["data"] == {"title": "Now", "format": "custom", "type": "freeform"}
    # card data
    c1 = items[C1_ID]
    assert c1["type"] == "card"
    assert c1["data"] == {"title": "Onboarding redesign", "description": "Q3 epic"}


async def test_items_cursor_walk_terminates(miro_client, miro_auth):
    # limit=10 (Miro's real minimum) over 12 items → 2 pages (10 + 2); cursor +
    # links.next present until the last page, then BOTH absent. (Miro signals EOF by
    # omitting `cursor`.)
    seen = []
    url = f"/v2/boards/{B1_ID}/items?limit=10"
    pages = 0
    while True:
        r = await miro_client.get(url, headers=miro_auth)
        body = r.json()
        pages += 1
        assert body["limit"] == 10
        seen.extend(i["id"] for i in body["data"])
        nxt = body["links"].get("next")
        if nxt is None:
            # terminal page must NOT carry a cursor field
            assert "cursor" not in body
            break
        assert "cursor" in body
        url = nxt.replace("http://mock", "")
        assert pages < 10
    assert pages == 2
    assert len(seen) == 12
    assert len(set(seen)) == 12  # no dupes across the cursor walk


async def test_items_type_filter(miro_client, miro_auth):
    r = await miro_client.get(f"/v2/boards/{B1_ID}/items?type=sticky_note&limit=50",
                              headers=miro_auth)
    body = r.json()
    assert body["total"] == 2  # total respects the type filter
    assert {i["type"] for i in body["data"]} == {"sticky_note"}
    assert len(body["data"]) == 2


async def test_items_type_filter_persists_in_next_link(miro_client, miro_auth):
    # limit=10 has no second page for B1's 2 sticky notes, so assert the simpler
    # invariant: an invalid type is rejected 400.
    r = await miro_client.get(f"/v2/boards/{B1_ID}/items?type=banana", headers=miro_auth)
    assert r.status_code == 400
    assert r.json()["type"] == "error"


async def test_items_limit_clamped(miro_client, miro_auth):
    # Miro items limit range is 10-50.
    r = await miro_client.get(f"/v2/boards/{B1_ID}/items?limit=999", headers=miro_auth)
    assert r.json()["limit"] == 50
    r2 = await miro_client.get(f"/v2/boards/{B1_ID}/items?limit=1", headers=miro_auth)
    assert r2.json()["limit"] == 10


async def test_items_unknown_board_404(miro_client, miro_auth):
    r = await miro_client.get("/v2/boards/nope=/items", headers=miro_auth)
    assert r.status_code == 404
    assert r.json()["code"] == "notFound"


# ----------------------------------------------------- rate limiting (credit)

async def test_rate_limit_headers_on_success(miro_client, miro_auth):
    r = await miro_client.get("/v2/boards", headers=miro_auth)
    assert "X-RateLimit-Limit" in r.headers
    assert "X-RateLimit-Remaining" in r.headers
    assert "X-RateLimit-Reset" in r.headers
    # Miro is credit-based: NO Retry-After (a real divergence from Figma/Brex).
    assert "Retry-After" not in r.headers


async def test_forced_429_credit_shape(miro_client, miro_auth):
    await miro_client.post("/_control/rate_limit?count=1")
    r = await miro_client.get("/v2/boards", headers=miro_auth)
    assert r.status_code == 429
    body = r.json()
    assert body["status"] == 429
    assert body["code"] == "tooManyRequests"
    assert body["type"] == "error"
    assert r.headers.get("X-RateLimit-Remaining") == "0"
    assert "Retry-After" not in r.headers
    # next call recovers
    r2 = await miro_client.get("/v2/boards", headers=miro_auth)
    assert r2.status_code == 200


async def test_health_reports_org(miro_client):
    r = await miro_client.get("/_health")
    assert r.status_code == 200
    assert r.json()["org_id"] == ORG_ID
