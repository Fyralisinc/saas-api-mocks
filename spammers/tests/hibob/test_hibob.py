"""Hard-fail fidelity tests for the HiBob mock (the REAL api.hibob.com contract).

These encode the wire facts pinned from HiBob's official developer docs
(apidocs.hibob.com) and FAIL on any divergence — the suite is an audit, not a
smoke test. Highest-risk facts (each the OPPOSITE of the Fyralis Gusto/Brex
clone): ``POST /v1/people/search`` returns ALL employees in one ``{employees:[…]}``
array with NO pagination; time-off is the ``/timeoff/requests/changes`` BARE ARRAY
windowed by ``since``/``to``; payroll history is ``/bulk/people/salaries`` with
CURSOR pagination (``{results, response_metadata:{next_cursor}}``); salary money is
``base:{value:<number>, currency}`` (a NUMBER, not cents/string); timestamps are
ISO no-Z µs; rate-limit is 429 + ``X-RateLimit-*`` with NO ``Retry-After``.
"""
from __future__ import annotations

import base64
import re

import pytest

from spammers.hibob.dto import (CHANGE_TYPES, PAY_PERIODS, PAY_FREQUENCIES)
from .conftest import SERVICE_USER_ID, SERVICE_USER_TOKEN

pytestmark = pytest.mark.asyncio(loop_scope="session")

_ISO_NOZ_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{1,6}$")
_DDMMYYYY_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_ISODATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# --------------------------------------------------------------------- auth

async def test_missing_auth_is_401_with_error_envelope(hibob_client):
    r = await hibob_client.post("/v1/people/search", json={})
    assert r.status_code == 401
    body = r.json()
    # HiBob error envelope: {error, message, statusCode, timestamp}
    assert set(body) >= {"error", "message", "statusCode"}
    assert body["statusCode"] == 401


async def test_bearer_is_not_a_hibob_scheme(hibob_client):
    r = await hibob_client.post("/v1/people/search", json={},
                                headers={"Authorization": f"Bearer {SERVICE_USER_TOKEN}"})
    assert r.status_code == 401


async def test_basic_requires_both_id_and_token(hibob_client):
    # An empty-password Basic header (id only) is not a valid HiBob credential.
    cred = base64.b64encode(f"{SERVICE_USER_ID}:".encode()).decode()
    r = await hibob_client.post("/v1/people/search", json={},
                                headers={"Authorization": f"Basic {cred}"})
    assert r.status_code == 401


# ----------------------------------------------------- people/search (no paging)

async def test_people_search_returns_all_active_in_one_envelope(hibob_client, hibob_auth):
    r = await hibob_client.post("/v1/people/search", headers=hibob_auth, json={})
    assert r.status_code == 200
    body = r.json()
    # {employees:[...]} — NO pagination metadata, NO cursor.
    assert set(body) == {"employees"}, sorted(body)
    emps = body["employees"]
    # default showInactive=false → only the 2 active employees.
    assert len(emps) == 2
    ids = {e["id"] for e in emps}
    assert ids == {"1001", "1002"}


async def test_people_search_show_inactive_includes_left(hibob_client, hibob_auth):
    r = await hibob_client.post("/v1/people/search", headers=hibob_auth,
                                json={"showInactive": True})
    assert r.status_code == 200
    ids = {e["id"] for e in r.json()["employees"]}
    assert ids == {"1001", "1002", "1003"}


async def test_people_search_filter_by_root_id(hibob_client, hibob_auth):
    r = await hibob_client.post("/v1/people/search", headers=hibob_auth, json={
        "filters": [{"fieldPath": "root.id", "operator": "equals", "values": ["1002"]}]})
    assert r.status_code == 200
    emps = r.json()["employees"]
    assert len(emps) == 1 and emps[0]["id"] == "1002"


async def test_people_search_filter_by_root_email(hibob_client, hibob_auth):
    r = await hibob_client.post("/v1/people/search", headers=hibob_auth, json={
        "filters": [{"fieldPath": "root.email", "operator": "equals",
                     "values": ["ada@example.com"]}]})
    assert r.status_code == 200
    emps = r.json()["employees"]
    assert len(emps) == 1 and emps[0]["email"] == "ada@example.com"


async def test_employee_object_shape(hibob_client, hibob_auth):
    r = await hibob_client.post("/v1/people/search", headers=hibob_auth, json={
        "filters": [{"fieldPath": "root.id", "operator": "equals", "values": ["1001"]}]})
    e = r.json()["employees"][0]
    # id + companyId are numeric strings.
    assert e["id"] == "1001" and isinstance(e["id"], str)
    assert e["companyId"] == "990001" and isinstance(e["companyId"], str)
    for k in ("firstName", "surname", "fullName", "displayName", "email"):
        assert isinstance(e[k], str)
    # creationDateTime is ISO-8601 microseconds with NO trailing Z.
    assert _ISO_NOZ_RE.match(e["creationDateTime"]), e["creationDateTime"]
    assert not e["creationDateTime"].endswith("Z")
    work = e["work"]
    assert isinstance(work, dict)
    # work.startDate renders DD/MM/YYYY (HiBob's human date form).
    assert _DDMMYYYY_RE.match(work["startDate"]), work["startDate"]
    # isManager is a "Yes"/"No" string (HiBob's wire form), not a bool.
    assert work["isManager"] in {"Yes", "No"}
    assert isinstance(e["about"], dict)


# ----------------------------------------------------- timeoff changes (window)

async def test_timeoff_changes_requires_since(hibob_client, hibob_auth):
    r = await hibob_client.get("/v1/timeoff/requests/changes", headers=hibob_auth)
    assert r.status_code == 400
    assert r.json()["statusCode"] == 400


async def test_timeoff_changes_is_bare_array(hibob_client, hibob_auth):
    r = await hibob_client.get("/v1/timeoff/requests/changes", headers=hibob_auth,
                               params={"since": "2026-01-01T00:00:00Z",
                                       "to": "2026-04-01T00:00:00Z"})
    assert r.status_code == 200
    body = r.json()
    # The response is a BARE ARRAY (no {requests:[…]} / no {data} envelope).
    assert isinstance(body, list)
    assert len(body) == 3
    item = body[0]
    assert isinstance(item["requestId"], int)
    assert item["changeType"] in CHANGE_TYPES
    assert _ISO_NOZ_RE.match(item["createdOn"]), item["createdOn"]
    assert _ISODATE_RE.match(item["startDate"]), item["startDate"]
    assert item["durationUnit"] in {"days", "hours"}


async def test_timeoff_changes_window_filters_by_created_on(hibob_client, hibob_auth):
    # A window that only covers February drops the Jan + Mar changes.
    r = await hibob_client.get("/v1/timeoff/requests/changes", headers=hibob_auth,
                               params={"since": "2026-02-01T00:00:00Z",
                                       "to": "2026-03-01T00:00:00Z"})
    assert r.status_code == 200
    ids = {c["requestId"] for c in r.json()}
    assert ids == {700002}, ids


async def test_timeoff_changes_window_over_six_months_is_400(hibob_client, hibob_auth):
    r = await hibob_client.get("/v1/timeoff/requests/changes", headers=hibob_auth,
                               params={"since": "2025-01-01T00:00:00Z",
                                       "to": "2026-04-01T00:00:00Z"})
    assert r.status_code == 400


# ----------------------------------------------------- bulk salaries (cursor)

async def test_salaries_envelope_and_money_number(hibob_client, hibob_auth):
    r = await hibob_client.get("/v1/bulk/people/salaries", headers=hibob_auth)
    assert r.status_code == 200
    body = r.json()
    # {results, response_metadata:{next_cursor}, errors} — the cursor envelope.
    assert set(body) == {"results", "response_metadata", "errors"}, sorted(body)
    assert set(body["response_metadata"]) == {"next_cursor"}
    assert isinstance(body["errors"], list)
    s = body["results"][0]
    base = s["base"]
    # base.value is a NUMBER in major units (120000), NOT cents (12000000) / NOT a string.
    assert isinstance(base["value"], (int, float)) and not isinstance(base["value"], bool)
    assert isinstance(base["currency"], str)
    assert s["payPeriod"] in PAY_PERIODS
    assert s["payFrequency"] in PAY_FREQUENCIES
    assert _ISODATE_RE.match(s["effectiveDate"]), s["effectiveDate"]
    assert isinstance(s["isCurrent"], bool)
    assert _ISO_NOZ_RE.match(s["creationDate"]), s["creationDate"]


async def test_salary_value_is_major_units_not_cents(hibob_client, hibob_auth):
    r = await hibob_client.get("/v1/bulk/people/salaries", headers=hibob_auth,
                               params={"employeeIds": "1001"})
    by_id = {s["id"]: s for s in r.json()["results"]}
    # 120_000_00 cents == $120,000 → base.value 120000 (a cents projection would be 12000000).
    assert by_id[501]["base"]["value"] == 120000


async def test_salaries_cursor_walk(hibob_client, hibob_auth):
    """Walk all 5 salary entries at limit=2: next_cursor present then null."""
    seen, cursor, pages = [], None, 0
    while pages < 10:
        params = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        r = await hibob_client.get("/v1/bulk/people/salaries", headers=hibob_auth,
                                   params=params)
        assert r.status_code == 200
        body = r.json()
        pages += 1
        seen.extend(s["id"] for s in body["results"])
        cursor = body["response_metadata"]["next_cursor"]
        if not cursor:
            break
    assert pages == 3            # 2 + 2 + 1
    assert sorted(seen) == [501, 502, 503, 504, 505]


async def test_salaries_limit_clamped_to_max(hibob_client, hibob_auth):
    r = await hibob_client.get("/v1/bulk/people/salaries", headers=hibob_auth,
                               params={"limit": 99999})
    assert r.status_code == 200  # clamped (max 200), not an error
    assert r.json()["response_metadata"]["next_cursor"] is None


async def test_salaries_employee_filter(hibob_client, hibob_auth):
    r = await hibob_client.get("/v1/bulk/people/salaries", headers=hibob_auth,
                               params={"employeeIds": "1001"})
    assert r.status_code == 200
    ids = {s["id"] for s in r.json()["results"]}
    assert ids == {501, 502}


# --------------------------------------------------------- rate limit + errors

async def test_forced_429_emits_ratelimit_headers_not_retry_after(hibob_client, hibob_auth):
    armed = await hibob_client.post("/_control/rate_limit", params={"count": 1})
    assert armed.status_code == 200
    r = await hibob_client.post("/v1/people/search", headers=hibob_auth, json={})
    assert r.status_code == 429
    # HiBob signals the window via X-RateLimit-* (Reset = epoch) and NOT Retry-After.
    assert r.headers.get("X-RateLimit-Reset") is not None
    assert r.headers.get("X-RateLimit-Remaining") == "0"
    assert "Retry-After" not in r.headers
    # next request recovers
    r2 = await hibob_client.post("/v1/people/search", headers=hibob_auth, json={})
    assert r2.status_code == 200
