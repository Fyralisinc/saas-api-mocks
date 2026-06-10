"""Hard-fail fidelity tests for the Figma mock (the REAL api.figma.com contract).

These encode the wire facts pinned from Figma's official developer docs + the
canonical OpenAPI spec (figma/rest-api-spec) and FAIL on any divergence — the suite
is an audit, not a smoke test. Highest-risk facts (each the OPPOSITE of the Fyralis
Brex-clone, which hits a single ``/v1/files/{key}/events`` that DOES NOT EXIST):
there is NO ``/v1/files`` list and NO ``/events`` — a real backfill enumerates files
(teams → projects → files) then MERGES ``/versions`` + ``/comments``; ``/versions``
paginates CURSOR-style (``page_size``/``before``, FULL-URL links); ``/comments`` has
NO pagination; the User object has NO ``email``; timestamps are UTC ISO-8601 with Z;
auth failure on file reads is 403 (not 401) with ``{status, err}``.
"""
from __future__ import annotations

import re

import pytest

from .conftest import (ACCESS_TOKEN, TEAM_ID, TEAM_NAME, ME_EMAIL, ME_ID,
                       P1_ID, P2_ID, F1_KEY, F2_KEY)

pytestmark = pytest.mark.asyncio(loop_scope="session")

_ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# --------------------------------------------------------------------- auth

async def test_missing_token_is_403_with_err_envelope(figma_client):
    r = await figma_client.get(f"/v1/files/{F1_KEY}/versions")
    # File-scoped reads document 403 (NOT 401) for a missing/invalid token.
    assert r.status_code == 403
    body = r.json()
    # err-message envelope {status, err} (NOT {error, status, message}).
    assert set(body) == {"status", "err"}, sorted(body)
    assert body["status"] == 403


async def test_x_figma_token_authenticates(figma_client, figma_auth):
    r = await figma_client.get(f"/v1/files/{F1_KEY}/comments", headers=figma_auth)
    assert r.status_code == 200


async def test_bearer_also_authenticates(figma_client):
    # Figma read endpoints accept OAuth Bearer too (alternate security scheme).
    r = await figma_client.get(f"/v1/files/{F1_KEY}/comments",
                               headers={"Authorization": f"Bearer {ACCESS_TOKEN}"})
    assert r.status_code == 200


# ----------------------------------------------------------- /v1/me

async def test_me_is_the_only_user_with_email(figma_client, figma_auth):
    r = await figma_client.get("/v1/me", headers=figma_auth)
    assert r.status_code == 200
    me = r.json()
    assert me["id"] == ME_ID
    assert me["email"] == ME_EMAIL          # /v1/me is the ONE place User carries email
    assert isinstance(me["handle"], str) and isinstance(me["img_url"], str)


# ----------------------------------------------------- teams -> projects -> files

async def test_team_projects_envelope(figma_client, figma_auth):
    r = await figma_client.get(f"/v1/teams/{TEAM_ID}/projects", headers=figma_auth)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"name", "projects"}, sorted(body)
    assert body["name"] == TEAM_NAME
    ids = {p["id"] for p in body["projects"]}
    assert ids == {P1_ID, P2_ID}
    for p in body["projects"]:
        assert set(p) == {"id", "name"}


async def test_wrong_team_id_is_404(figma_client, figma_auth):
    r = await figma_client.get("/v1/teams/does-not-exist/projects", headers=figma_auth)
    assert r.status_code == 404
    assert r.json()["status"] == 404


async def test_no_global_files_list_endpoint(figma_client, figma_auth):
    # The Fyralis clone hits GET /v1/files — that endpoint DOES NOT EXIST in Figma.
    r = await figma_client.get("/v1/files", headers=figma_auth)
    assert r.status_code == 404


async def test_project_files_listing(figma_client, figma_auth):
    r = await figma_client.get(f"/v1/projects/{P1_ID}/files", headers=figma_auth)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"name", "files"}, sorted(body)
    assert body["name"] == "Product"
    f = body["files"][0]
    assert set(f) == {"key", "name", "thumbnail_url", "last_modified"}
    assert f["key"] == F1_KEY
    assert _ISO_Z_RE.match(f["last_modified"]), f["last_modified"]


async def test_no_events_endpoint(figma_client, figma_auth):
    # The Fyralis clone's GET /v1/files/{key}/events DOES NOT EXIST.
    r = await figma_client.get(f"/v1/files/{F1_KEY}/events", headers=figma_auth)
    assert r.status_code == 404


# --------------------------------------------------------- file meta

async def test_file_meta_is_wrapped_in_file(figma_client, figma_auth):
    r = await figma_client.get(f"/v1/files/{F1_KEY}/meta", headers=figma_auth)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"file"}             # wrapped in {file:{…}}
    f = body["file"]
    assert f["name"] == "Onboarding Flow"
    assert f["version"] == "1004"            # /meta `version` = newest version id
    assert _ISO_Z_RE.match(f["last_touched_at"]), f["last_touched_at"]
    assert f["editorType"] == "figma"
    # creator is a User with NO email.
    assert "email" not in f["creator"]
    assert set(f["creator"]) == {"id", "handle", "img_url"}


async def test_file_not_found_is_404(figma_client, figma_auth):
    r = await figma_client.get("/v1/files/NOPEnopeNOPE/meta", headers=figma_auth)
    assert r.status_code == 404
    assert r.json()["status"] == 404


# --------------------------------------------------------- versions

async def test_versions_envelope_and_object_shape(figma_client, figma_auth):
    r = await figma_client.get(f"/v1/files/{F1_KEY}/versions", headers=figma_auth)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"versions", "pagination"}, sorted(body)
    assert isinstance(body["pagination"], dict)
    vs = body["versions"]
    # default page_size 30 > 5 versions → all 5, newest-first.
    assert [v["id"] for v in vs] == ["1004", "1003", "1002", "1001", "1000"]
    v = vs[0]
    assert isinstance(v["id"], str)
    assert _ISO_Z_RE.match(v["created_at"]), v["created_at"]
    # label/description may be present-but-null (auto-saves); user has NO email.
    assert "label" in v and "description" in v
    assert set(v["user"]) == {"id", "handle", "img_url"}
    assert "email" not in v["user"]
    # an auto-save version carries label=null, description=null (not dropped).
    autosave = next(x for x in vs if x["id"] == "1003")
    assert autosave["label"] is None and autosave["description"] is None


async def test_versions_cursor_walk_with_page_size(figma_client, figma_auth):
    """Walk all 5 versions at page_size=2: next_page present then absent."""
    seen, url = [], f"/v1/files/{F1_KEY}/versions?page_size=2"
    pages = 0
    while url and pages < 10:
        r = await figma_client.get(url, headers=figma_auth)
        assert r.status_code == 200
        body = r.json()
        pages += 1
        seen.extend(v["id"] for v in body["versions"])
        nxt = body["pagination"].get("next_page")
        if not nxt:
            break
        # next_page is a FULL URL — assert it carries before=, then follow its path+query.
        assert "before=" in nxt
        url = nxt.split("http://mock", 1)[-1] if "http://mock" in nxt else nxt
    assert pages == 3                                   # 2 + 2 + 1
    assert seen == ["1004", "1003", "1002", "1001", "1000"]


async def test_versions_before_filters_older(figma_client, figma_auth):
    r = await figma_client.get(f"/v1/files/{F1_KEY}/versions",
                               headers=figma_auth, params={"before": 1002})
    ids = [v["id"] for v in r.json()["versions"]]
    assert ids == ["1001", "1000"]                      # strictly older than 1002


async def test_versions_page_size_over_max_is_clamped_not_error(figma_client, figma_auth):
    r = await figma_client.get(f"/v1/files/{F1_KEY}/versions",
                               headers=figma_auth, params={"page_size": 99999})
    assert r.status_code == 200                          # clamped to 50, not a 400
    assert len(r.json()["versions"]) == 5


async def test_versions_bad_before_is_400(figma_client, figma_auth):
    r = await figma_client.get(f"/v1/files/{F1_KEY}/versions",
                               headers=figma_auth, params={"before": "abc"})
    assert r.status_code == 400
    assert r.json()["status"] == 400


# --------------------------------------------------------- comments

async def test_comments_is_unpaginated_array_envelope(figma_client, figma_auth):
    r = await figma_client.get(f"/v1/files/{F1_KEY}/comments", headers=figma_auth)
    assert r.status_code == 200
    body = r.json()
    # {comments:[…]} — NO pagination object, all comments in one array.
    assert set(body) == {"comments"}, sorted(body)
    cs = body["comments"]
    assert {c["id"] for c in cs} == {"8001", "8002", "8003"}
    c = next(x for x in cs if x["id"] == "8001")
    assert c["file_key"] == F1_KEY
    assert _ISO_Z_RE.match(c["created_at"]), c["created_at"]
    assert isinstance(c["message"], str) and c["message"]
    # order_id is a string|null (the OpenAPI spec — NOT a Number).
    assert isinstance(c["order_id"], str)
    # user has NO email.
    assert set(c["user"]) == {"id", "handle", "img_url"}
    # client_meta Vector pin.
    assert set(c["client_meta"]) == {"x", "y"}
    # a reaction round-trips with its emoji shortcode.
    assert c["reactions"][0]["emoji"] == ":+1:"


async def test_comment_reply_has_parent_id(figma_client, figma_auth):
    r = await figma_client.get(f"/v1/files/{F1_KEY}/comments", headers=figma_auth)
    cs = {c["id"]: c for c in r.json()["comments"]}
    # 8002 is a reply to 8001; its client_meta is a FrameOffset (node-relative).
    assert cs["8002"]["parent_id"] == "8001"
    assert set(cs["8002"]["client_meta"]) == {"node_id", "node_offset"}
    # a root comment carries an empty parent_id.
    assert cs["8001"]["parent_id"] == ""


async def test_comment_resolved_at_nullable(figma_client, figma_auth):
    r = await figma_client.get(f"/v1/files/{F1_KEY}/comments", headers=figma_auth)
    cs = {c["id"]: c for c in r.json()["comments"]}
    assert cs["8003"]["resolved_at"] is not None and _ISO_Z_RE.match(cs["8003"]["resolved_at"])
    assert cs["8001"]["resolved_at"] is None             # unresolved → null


# --------------------------------------------------------- rate limit

async def test_forced_429_emits_retry_after(figma_client, figma_auth):
    armed = await figma_client.post("/_control/rate_limit", params={"count": 1})
    assert armed.status_code == 200
    r = await figma_client.get(f"/v1/files/{F1_KEY}/comments", headers=figma_auth)
    assert r.status_code == 429
    # Figma DOES document Retry-After + plan/tier headers (a divergence from HiBob).
    assert r.headers.get("Retry-After") == "1"
    assert r.headers.get("X-Figma-Plan-Tier") is not None
    assert r.json()["status"] == 429
    # next request recovers.
    r2 = await figma_client.get(f"/v1/files/{F1_KEY}/comments", headers=figma_auth)
    assert r2.status_code == 200
