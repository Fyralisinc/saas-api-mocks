"""Hard-fail fidelity tests for the Gusto mock (the REAL api.gusto.com /v1 contract).

Encodes the load-bearing wire facts a connector depends on — if any diverges from
real Gusto, the consumer that passes here would break against production:
  * list endpoints return a BARE JSON ARRAY (no envelope) + pagination metadata in
    the ``X-Page``/``X-Total-Count``/``X-Total-Pages``/``X-Per-Page`` headers (no Link);
  * money is a decimal STRING in dollars (NOT cents, NOT a number);
  * datetimes are ISO-8601 ``Z``; date-only fields are ``YYYY-MM-DD``;
  * the payrolls list defaults to a 6-month window + rejects a >1-year span (422);
  * ``X-Gusto-API-Version`` is echoed; 429 carries Retry-After + X-RateLimit-* (the
    real contrast with hibob/ramp/miro); errors use the ``{"errors":[…]}`` envelope.
"""
from __future__ import annotations

import re

import pytest

from .conftest import ACCESS_TOKEN, COMPANY_UUID

pytestmark = pytest.mark.asyncio(loop_scope="session")

_BASE = f"/v1/companies/{COMPANY_UUID}"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONEY = re.compile(r"^-?\d+\.\d{2}$")
_ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# ----------------------------------------------------------------------- auth

async def test_missing_bearer_is_401_invalid_token(gusto_client):
    r = await gusto_client.get(f"{_BASE}/employees")
    assert r.status_code == 401, r.text
    body = r.json()
    assert "errors" in body and isinstance(body["errors"], list)
    assert body["errors"][0]["category"] == "invalid_token"
    assert r.headers.get("X-Gusto-API-Version")  # echoed even on error


async def test_bearer_ok_and_api_version_echoed(gusto_client, gusto_auth):
    r = await gusto_client.get(f"{_BASE}/employees", headers=gusto_auth)
    assert r.status_code == 200
    # default version echoed when the request omits the header
    assert r.headers.get("X-Gusto-API-Version") == "2024-04-01"
    # an explicit version is echoed back verbatim
    r2 = await gusto_client.get(f"{_BASE}/employees",
                                headers={**gusto_auth, "X-Gusto-API-Version": "2025-11-15"})
    assert r2.headers.get("X-Gusto-API-Version") == "2025-11-15"


async def test_token_mint(gusto_client):
    r = await gusto_client.post("/oauth/token",
                                json={"grant_type": "authorization_code", "code": "abc",
                                      "client_id": "x", "client_secret": "y"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 7200
    assert body["access_token"] == ACCESS_TOKEN
    assert "refresh_token" in body and isinstance(body["created_at"], int)


async def test_token_mint_bad_grant_400(gusto_client):
    r = await gusto_client.post("/oauth/token", json={"grant_type": "password"})
    assert r.status_code == 400
    assert r.json()["errors"][0]["category"] == "invalid_request"


# -------------------------------------------------------------------- company

async def test_company_object(gusto_client, gusto_auth):
    r = await gusto_client.get(_BASE, headers=gusto_auth)
    assert r.status_code == 200, r.text
    co = r.json()
    assert co["uuid"] == COMPANY_UUID
    assert co["name"] == "Alpen Labs Inc."
    assert co["entity_type"] == "C-Corporation"
    assert _DATE.match(co["join_date"])


async def test_company_wrong_uuid_404(gusto_client, gusto_auth):
    r = await gusto_client.get("/v1/companies/deadbeef-0000-4000-8000-000000000000",
                               headers=gusto_auth)
    assert r.status_code == 404
    assert r.json()["errors"][0]["category"] == "not_found"


# ------------------------------------------------------------------ employees

async def test_employees_bare_array_and_pagination_headers(gusto_client, gusto_auth):
    r = await gusto_client.get(f"{_BASE}/employees", headers=gusto_auth)
    assert r.status_code == 200, r.text
    body = r.json()
    # BARE ARRAY body — NOT an envelope object.
    assert isinstance(body, list)
    assert len(body) == 3
    # pagination metadata is in the HEADERS, not the body.
    assert r.headers["X-Total-Count"] == "3"
    assert r.headers["X-Page"] == "1"
    assert r.headers["X-Per-Page"] == "25"
    assert r.headers["X-Total-Pages"] == "1"
    # NO Link header (Gusto page-based pagination has none).
    assert "Link" not in r.headers


async def test_employees_offset_paginates(gusto_client, gusto_auth):
    p1 = await gusto_client.get(f"{_BASE}/employees", params={"per": 2, "page": 1},
                                headers=gusto_auth)
    p2 = await gusto_client.get(f"{_BASE}/employees", params={"per": 2, "page": 2},
                                headers=gusto_auth)
    assert [len(p1.json()), len(p2.json())] == [2, 1]
    assert p1.headers["X-Total-Pages"] == "2"
    # no overlap between pages
    ids1 = {e["uuid"] for e in p1.json()}
    ids2 = {e["uuid"] for e in p2.json()}
    assert ids1.isdisjoint(ids2)


async def test_employee_shape_and_money_is_string(gusto_client, gusto_auth):
    r = await gusto_client.get(f"{_BASE}/employees", params={"per": 1}, headers=gusto_auth)
    emp = r.json()[0]
    assert emp["uuid"] and isinstance(emp["version"], str)
    assert "current_employment_status" in emp
    assert _DATE.match(emp["date_of_birth"])
    job = emp["jobs"][0]
    # rate is a money STRING in dollars (NOT cents, NOT a number)
    assert isinstance(job["rate"], str) and _MONEY.match(job["rate"]), job["rate"]
    assert not isinstance(job["rate"], (int, float))
    comp = job["compensations"][0]
    assert isinstance(comp["rate"], str) and _MONEY.match(comp["rate"])
    assert comp["flsa_status"] in ("Exempt", "Nonexempt")


async def test_employees_terminated_filter(gusto_client, gusto_auth):
    r = await gusto_client.get(f"{_BASE}/employees", params={"terminated": "true"},
                               headers=gusto_auth)
    body = r.json()
    assert len(body) == 1 and body[0]["terminated"] is True
    assert r.headers["X-Total-Count"] == "1"


# ------------------------------------------------------------------- payrolls

async def test_payrolls_default_6mo_window(gusto_client, gusto_auth):
    # default (no start_date) returns only the last ~6 months → the 5 recent runs,
    # NOT the 2 older-than-a-year ones.
    r = await gusto_client.get(f"{_BASE}/payrolls", headers=gusto_auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, list)
    assert r.headers["X-Total-Count"] == "5"
    assert len(body) == 5


async def test_payrolls_explicit_window_reaches_old_runs(gusto_client, gusto_auth):
    r = await gusto_client.get(f"{_BASE}/payrolls",
                               params={"start_date": "2024-01-01", "end_date": "2024-12-31"},
                               headers=gusto_auth)
    assert r.status_code == 200
    assert r.headers["X-Total-Count"] == "2"


async def test_payrolls_span_over_one_year_422(gusto_client, gusto_auth):
    r = await gusto_client.get(f"{_BASE}/payrolls",
                               params={"start_date": "2023-01-01", "end_date": "2026-02-01"},
                               headers=gusto_auth)
    assert r.status_code == 422
    assert r.json()["errors"][0]["category"] == "invalid_parameter"


async def test_payrolls_shape_dates_and_totals_gate(gusto_client, gusto_auth):
    # default omits `totals`
    r = await gusto_client.get(f"{_BASE}/payrolls", params={"per": 1}, headers=gusto_auth)
    p = r.json()[0]
    assert _DATE.match(p["check_date"])
    assert _DATE.match(p["pay_period"]["start_date"])
    assert _DATE.match(p["pay_period"]["end_date"])
    assert _ISO_Z.match(p["calculated_at"])
    assert p["processed"] is True
    assert "totals" not in p
    # include=totals adds the money-STRING totals
    r2 = await gusto_client.get(f"{_BASE}/payrolls",
                                params={"per": 1, "include": "totals"}, headers=gusto_auth)
    t = r2.json()[0]["totals"]
    assert isinstance(t["gross_pay"], str) and _MONEY.match(t["gross_pay"])


async def test_payrolls_offset_paginates(gusto_client, gusto_auth):
    p1 = await gusto_client.get(f"{_BASE}/payrolls", params={"per": 2, "page": 1},
                                headers=gusto_auth)
    assert len(p1.json()) == 2
    assert p1.headers["X-Total-Count"] == "5"
    assert p1.headers["X-Total-Pages"] == "3"


async def test_payrolls_unprocessed_status_empty(gusto_client, gusto_auth):
    r = await gusto_client.get(f"{_BASE}/payrolls",
                               params={"processing_statuses": "unprocessed"},
                               headers=gusto_auth)
    assert r.headers["X-Total-Count"] == "0"


async def test_single_payroll_has_employee_compensations(gusto_client, gusto_auth):
    lst = await gusto_client.get(f"{_BASE}/payrolls", params={"per": 1}, headers=gusto_auth)
    puid = lst.json()[0]["uuid"]
    r = await gusto_client.get(f"{_BASE}/payrolls/{puid}", headers=gusto_auth)
    assert r.status_code == 200, r.text
    detail = r.json()
    assert "totals" in detail
    comps = detail["employee_compensations"]
    assert isinstance(comps, list) and comps
    c0 = comps[0]
    assert c0["employee_uuid"]
    assert isinstance(c0["gross_pay"], str) and _MONEY.match(c0["gross_pay"])


async def test_single_payroll_unknown_404(gusto_client, gusto_auth):
    r = await gusto_client.get(f"{_BASE}/payrolls/nope-0000-4000-8000-000000000000",
                               headers=gusto_auth)
    assert r.status_code == 404


# ------------------------------------------------------------------ controls

async def test_per_clamps_to_100(gusto_client, gusto_auth):
    r = await gusto_client.get(f"{_BASE}/employees", params={"per": 9999}, headers=gusto_auth)
    assert r.headers["X-Per-Page"] == "100"


async def test_bad_page_param_422(gusto_client, gusto_auth):
    r = await gusto_client.get(f"{_BASE}/employees", params={"per": "abc"}, headers=gusto_auth)
    assert r.status_code == 422


async def test_rate_limit_429_has_retry_after_and_ratelimit_headers(gusto_client, gusto_auth):
    # Gusto DOES publish Retry-After + X-RateLimit-* on 429 (the real contrast with
    # hibob/ramp/miro, which publish neither).
    await gusto_client.post("/_control/rate_limit", params={"count": 1})
    r = await gusto_client.get(f"{_BASE}/employees", headers=gusto_auth)
    assert r.status_code == 429
    assert r.headers.get("Retry-After") == "30"
    assert r.headers.get("X-RateLimit-Limit") == "200"
    assert "X-RateLimit-Reset" in r.headers
    assert r.json()["errors"][0]["category"] == "rate_limit_exceeded"
    # recovers on the next call
    r2 = await gusto_client.get(f"{_BASE}/employees", headers=gusto_auth)
    assert r2.status_code == 200
