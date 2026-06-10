"""Ashby mock fidelity — hard-fail audit vs Ashby's first-party API contract.

Each assertion encodes a documented wire fact; a red test is a fidelity gap, not a
flaky mock. Audited vs developer.ashbyhq.com (OpenAPI 3.1 + prose docs):
  * RPC: every read is an HTTP POST to ``/<category>.<verb>``.
  * Auth: HTTP Basic, API key as username, EMPTY password; Basic-only (no Bearer).
  * Envelope: ``.list``/``.search`` → {success, results:[…], moreDataAvailable,
    nextCursor?, syncToken?}; ``.info`` → {success, results:{…}} (object).
  * Business errors are HTTP 200 + success:false ({errors:[…], errorInfo:{code,…}}).
  * Pagination: opaque cursor; limit default+max 100; nextCursor only while more;
    syncToken minted on the terminal page; an incremental sync filters updated_at.
  * Timestamps: ISO-8601 UTC with millisecond precision + Z.
"""
from __future__ import annotations

import json
import re
from datetime import timezone
from uuid import UUID, uuid4

import pytest

from spammers.ashby import dto as _dto
from .conftest import API_KEY, APP, APP_SPEC, CAND, basic_header

pytestmark = pytest.mark.asyncio(loop_scope="session")

_ISO_MS_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


async def _list(client, category, auth, **body):
    return await client.post(f"/{category}.list", headers=auth, json=body)


# ---------------------------------------------------------------- auth

async def test_missing_auth_is_401(ashby_client):
    r = await ashby_client.post("/candidate.list", json={})
    assert r.status_code == 401
    body = r.json()
    assert body["success"] is False


async def test_bearer_is_rejected_ashby_is_basic_only(ashby_client):
    # Ashby authenticates with HTTP Basic ONLY — a Bearer header is not the scheme.
    r = await ashby_client.post("/candidate.list", headers={"Authorization": f"Bearer {API_KEY}"},
                                json={})
    assert r.status_code == 401


async def test_basic_auth_empty_password_accepted(ashby_client, ashby_auth):
    r = await ashby_client.post("/candidate.list", headers=ashby_auth, json={})
    assert r.status_code == 200
    assert r.json()["success"] is True


# ---------------------------------------------------------------- envelopes

async def test_list_envelope_results_is_array(ashby_client, ashby_auth):
    r = await _list(ashby_client, "candidate", ashby_auth)
    body = r.json()
    assert body["success"] is True
    assert isinstance(body["results"], list)
    assert isinstance(body["moreDataAvailable"], bool)
    assert len(body["results"]) == len(CAND)


async def test_info_envelope_results_is_object(ashby_client, ashby_auth):
    r = await ashby_client.post("/candidate.info", headers=ashby_auth,
                                json={"id": str(CAND[0])})
    body = r.json()
    assert body["success"] is True
    assert isinstance(body["results"], dict), ".info returns a single OBJECT, not an array"
    assert body["results"]["id"] == str(CAND[0])


async def test_application_status_enum(ashby_client, ashby_auth):
    r = await _list(ashby_client, "application", ashby_auth)
    statuses = {a["status"] for a in r.json()["results"]}
    assert statuses <= _dto.APPLICATION_STATUSES
    assert {"Active", "Hired", "Archived", "Lead"} <= statuses


async def test_timestamps_are_iso_millis_z(ashby_client, ashby_auth):
    r = await _list(ashby_client, "candidate", ashby_auth)
    for c in r.json()["results"]:
        assert _ISO_MS_Z.match(c["createdAt"]), f"bad createdAt {c['createdAt']!r}"
        assert _ISO_MS_Z.match(c["updatedAt"]), f"bad updatedAt {c['updatedAt']!r}"


# ---------------------------------------------------------------- pagination

async def test_cursor_pagination_walk(ashby_client, ashby_auth):
    seen: list[str] = []
    cursor = None
    pages = 0
    saw_sync = None
    while True:
        body = (await _list(ashby_client, "application", ashby_auth,
                            limit=2, **({"cursor": cursor} if cursor else {}))).json()
        pages += 1
        seen.extend(a["id"] for a in body["results"])
        if body["moreDataAvailable"]:
            assert "nextCursor" in body and body["nextCursor"], "nextCursor required while more"
            assert "syncToken" not in body, "syncToken only on the terminal page"
            cursor = body["nextCursor"]
        else:
            assert "nextCursor" not in body, "no nextCursor on the terminal page"
            saw_sync = body.get("syncToken")
            break
    assert len(seen) == len(APP) == 5
    assert len(set(seen)) == 5, "no dupes across pages"
    assert pages == 3, "5 items at limit=2 → 3 pages"
    assert saw_sync, "terminal page mints a syncToken"


async def test_limit_above_max_is_clamped_not_errored(ashby_client, ashby_auth):
    body = (await _list(ashby_client, "application", ashby_auth, limit=500)).json()
    assert body["success"] is True
    assert body["moreDataAvailable"] is False
    assert len(body["results"]) == 5  # clamped to <=100, all returned in one page


# ---------------------------------------------------------------- syncToken

async def test_synctoken_round_trip_then_incremental(ashby_client, ashby_auth, pool, ashby_run):
    # Full walk → capture the minted syncToken.
    body = (await _list(ashby_client, "application", ashby_auth, limit=100)).json()
    assert body["moreDataAvailable"] is False
    token = body["syncToken"]
    assert token

    # Re-sync with the token: nothing changed since the high-water → 0 results.
    body2 = (await _list(ashby_client, "application", ashby_auth, syncToken=token)).json()
    assert body2["results"] == [], "an unchanged incremental sync returns no rows"
    assert body2["moreDataAvailable"] is False

    # Introduce a change strictly after the floor, then re-sync → exactly that row.
    org_pk = await pool.fetchval(
        "SELECT id FROM app_ashby.organizations WHERE run_id=$1", ashby_run)
    new_id = uuid4()
    from .conftest import VNOW
    data = _dto.application_dto(
        entity_id=str(new_id), created_at=VNOW, updated_at=VNOW, status="Active",
        candidate_ref={"id": str(CAND[0]), "name": "X",
                       "primaryEmailAddress": None, "primaryPhoneNumber": None},
        current_stage={"id": str(uuid4()), "title": "Application Review",
                       "type": "PreInterviewScreen", "orderInInterviewPlan": 0,
                       "interviewStageGroupId": str(uuid4()), "interviewPlanId": str(uuid4())},
        job_ref={"id": "x", "title": "x", "locationId": "x", "departmentId": "x"},
        source=None, hiring_team=[])
    await pool.execute(
        """INSERT INTO app_ashby.entities
            (id, org_pk, kind, entity_id, status, data, created_at, updated_at, is_historical)
           VALUES ($1,$2,'application',$1,'Active',$3::jsonb,$4,$4,FALSE)""",
        new_id, org_pk, json.dumps(data), VNOW)
    try:
        body3 = (await _list(ashby_client, "application", ashby_auth, syncToken=token)).json()
        ids = [a["id"] for a in body3["results"]]
        assert ids == [str(new_id)], "incremental sync returns only the changed row"
    finally:
        await pool.execute("DELETE FROM app_ashby.entities WHERE entity_id=$1", new_id)


# ---------------------------------------------------------------- errors

async def test_unknown_id_is_business_error_at_http_200(ashby_client, ashby_auth):
    r = await ashby_client.post("/application.info", headers=ashby_auth,
                                json={"id": str(uuid4())})
    assert r.status_code == 200, "Ashby business errors come back at HTTP 200"
    body = r.json()
    assert body["success"] is False
    assert isinstance(body["errors"], list) and body["errors"], "errors[] (deprecated codes)"
    assert body["errorInfo"]["code"], "errorInfo.code is the structured code"


async def test_unknown_endpoint_is_404(ashby_client, ashby_auth):
    r = await ashby_client.post("/candidate.frobnicate", headers=ashby_auth, json={})
    assert r.status_code == 404
    assert r.json()["success"] is False


async def test_malformed_cursor_is_business_error(ashby_client, ashby_auth):
    r = await _list(ashby_client, "application", ashby_auth, cursor="!!not-base64!!")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert body["errorInfo"]["code"] == "invalid_cursor"


# ---------------------------------------------------------------- rate limit

async def test_forced_429_carries_retry_after(ashby_client, ashby_auth):
    await ashby_client.post("/_control/rate_limit", json={"count": 1})
    r = await _list(ashby_client, "candidate", ashby_auth)
    assert r.status_code == 429
    assert r.headers.get("Retry-After") == "1"
    # the armed 429 is consumed; the next call succeeds again
    r2 = await _list(ashby_client, "candidate", ashby_auth)
    assert r2.status_code == 200
