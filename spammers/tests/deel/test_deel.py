"""Hard-fail fidelity tests for the Deel mock (the REAL api.letsdeel.com/rest/v2 contract).

These encode the wire facts pinned from Deel's official developer docs and FAIL on
any divergence — the suite is an audit, not a smoke test. Highest-risk facts (each
the OPPOSITE of the Fyralis Mercury-clone): the {data, page} envelope (NOT
{total, payments}), CURSOR-only contracts vs HYBRID invoices, money as decimal
STRINGS in major units (NOT cents / NOT a number), the paid-only-vs-status=all
invoice filter, RFC3339-ms-Z timestamps, and the {request, errors} error envelope.
"""
from __future__ import annotations

import re

import pytest

from spammers.deel.dto import CONTRACT_TYPES, CONTRACT_STATUSES, INVOICE_STATUSES
from .conftest import API_TOKEN

pytestmark = pytest.mark.asyncio(loop_scope="session")

_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONEY_RE = re.compile(r"^-?\d+\.\d{2}$")


def _is_money_str(v) -> bool:
    return isinstance(v, str) and bool(_MONEY_RE.match(v))


# --------------------------------------------------------------------- auth

async def test_missing_auth_is_401_with_error_envelope(deel_client):
    r = await deel_client.get("/rest/v2/contracts")
    assert r.status_code == 401
    body = r.json()
    # Deel error envelope: {request:{...}, errors:[{message,...}]}
    assert isinstance(body.get("request"), dict)
    assert isinstance(body.get("errors"), list) and body["errors"]
    assert "message" in body["errors"][0]


async def test_basic_auth_is_not_a_deel_scheme(deel_client):
    import base64
    tok = base64.b64encode(f"{API_TOKEN}:".encode()).decode()
    r = await deel_client.get("/rest/v2/contracts", headers={"Authorization": f"Basic {tok}"})
    assert r.status_code == 401


async def test_bearer_auth_ok_and_version_echo(deel_client, deel_auth):
    r = await deel_client.get("/rest/v2/contracts", headers=deel_auth)
    assert r.status_code == 200
    # Deel resolves + echoes the date-based API version header.
    assert r.headers.get("X-Version") == "2026-01-01"


# --------------------------------------------------------------- contracts

async def test_contracts_envelope_and_object_shape(deel_client, deel_auth):
    r = await deel_client.get("/rest/v2/contracts", headers=deel_auth)
    assert r.status_code == 200
    body = r.json()
    # {data:[...], page:{cursor, total_rows}} — NOT {total, contracts}
    assert set(body) == {"data", "page"}, sorted(body)
    assert set(body["page"]) == {"cursor", "total_rows"}, sorted(body["page"])
    assert body["page"]["total_rows"] == 3
    assert isinstance(body["data"], list) and len(body["data"]) == 3
    for c in body["data"]:
        assert c["type"] in CONTRACT_TYPES, c["type"]
        assert c["status"] in CONTRACT_STATUSES, c["status"]
        assert isinstance(c["worker"], dict) and "full_name" in c["worker"]
        comp = c["compensation_details"]
        assert _is_money_str(comp["amount"]), comp["amount"]
        assert isinstance(comp["currency_code"], str)
        assert _TS_RE.match(c["created_at"]), c["created_at"]
        assert _TS_RE.match(c["updated_at"]), c["updated_at"]
        # start_date is DATE-only; termination_date is DATE-only or null
        assert _DATE_RE.match(c["start_date"]), c["start_date"]
        assert c["termination_date"] is None or _DATE_RE.match(c["termination_date"])


async def test_contract_compensation_is_decimal_string_not_cents(deel_client, deel_auth):
    r = await deel_client.get("/rest/v2/contracts", headers=deel_auth)
    by_id = {c["id"]: c for c in r.json()["data"]}
    # Seeded 900_000 cents == $9,000.00 → "9000.00" (a cents projection would be 900000).
    amt = by_id["ctr_alpha01"]["compensation_details"]["amount"]
    assert amt == "9000.00" and isinstance(amt, str)


async def test_contracts_cursor_walk(deel_client, deel_auth):
    """Two-page CURSOR walk at limit=2: page.cursor present then null; complete set."""
    seen, cursor, pages = [], None, 0
    while pages < 10:
        params = {"limit": 2}
        if cursor:
            params["after_cursor"] = cursor
        r = await deel_client.get("/rest/v2/contracts", headers=deel_auth, params=params)
        assert r.status_code == 200
        body = r.json()
        pages += 1
        seen.extend(c["id"] for c in body["data"])
        cursor = body["page"]["cursor"]
        if not cursor:
            break
    assert pages == 2
    assert seen == ["ctr_alpha01", "ctr_bravo02", "ctr_charlie3"]


async def test_single_contract_is_wrapped_in_data(deel_client, deel_auth):
    r = await deel_client.get("/rest/v2/contracts/ctr_bravo02", headers=deel_auth)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"data"}
    assert body["data"]["id"] == "ctr_bravo02"
    assert body["data"]["status"] == "completed"
    assert body["data"]["termination_date"] == "2026-02-15"


async def test_unknown_contract_is_404(deel_client, deel_auth):
    r = await deel_client.get("/rest/v2/contracts/ctr_nope", headers=deel_auth)
    assert r.status_code == 404
    assert isinstance(r.json().get("errors"), list)


# ---------------------------------------------------------------- invoices

async def test_invoices_default_returns_paid_only(deel_client, deel_auth):
    # No status param → ONLY paid invoices (the 3 paid ones), NOT all 5.
    r = await deel_client.get("/rest/v2/invoices", headers=deel_auth)
    assert r.status_code == 200
    body = r.json()
    assert body["page"]["total_rows"] == 3
    assert all(inv["status"] == "paid" for inv in body["data"])


async def test_invoices_status_all_returns_every_status(deel_client, deel_auth):
    r = await deel_client.get("/rest/v2/invoices", headers=deel_auth,
                              params={"status": "all"})
    assert r.status_code == 200
    body = r.json()
    assert body["page"]["total_rows"] == 5
    statuses = {inv["status"] for inv in body["data"]}
    assert "pending" in statuses and "processing" in statuses and "paid" in statuses


async def test_invoices_envelope_and_object_shape(deel_client, deel_auth):
    r = await deel_client.get("/rest/v2/invoices", headers=deel_auth,
                              params={"status": "all"})
    body = r.json()
    # HYBRID page metadata: offset + total_rows + items_per_page + cursor
    assert set(body) == {"data", "page"}, sorted(body)
    assert set(body["page"]) == {"offset", "total_rows", "items_per_page", "cursor"}, \
        sorted(body["page"])
    for inv in body["data"]:
        assert inv["status"] in INVOICE_STATUSES, inv["status"]
        assert _is_money_str(inv["total"]) and _is_money_str(inv["amount"])
        assert isinstance(inv["currency"], str)
        assert isinstance(inv["contract_id"], str) and inv["contract_id"].startswith("ctr_")
        assert _TS_RE.match(inv["issued_at"]), inv["issued_at"]
        assert isinstance(inv["is_overdue"], bool)
        # paid invoices carry a paid_at; unpaid ones are null
        if inv["status"] == "paid":
            assert _TS_RE.match(inv["paid_at"]), inv["paid_at"]
        else:
            assert inv["paid_at"] is None


async def test_invoice_amount_is_decimal_string_not_cents(deel_client, deel_auth):
    r = await deel_client.get("/rest/v2/invoices", headers=deel_auth,
                              params={"status": "all"})
    by_id = {inv["id"]: inv for inv in r.json()["data"]}
    # 900_000 cents == $9,000.00 → "9000.00"
    assert by_id["inv_p001"]["amount"] == "9000.00"


async def test_invoices_default_limit_is_25(deel_client, deel_auth):
    # The documented invoices default page size is 25 — items_per_page reflects it
    # even though fewer rows exist.
    r = await deel_client.get("/rest/v2/invoices", headers=deel_auth,
                              params={"status": "all"})
    assert r.json()["page"]["items_per_page"] == 25


async def test_invoices_offset_cursor_walk(deel_client, deel_auth):
    """Hybrid walk at limit=2 over all 5 invoices: offset advances, cursor terminates."""
    seen, cursor, offset_seen, pages = [], None, [], 0
    while pages < 10:
        params = {"limit": 2, "status": "all"}
        if cursor:
            params["cursor"] = cursor
        r = await deel_client.get("/rest/v2/invoices", headers=deel_auth, params=params)
        assert r.status_code == 200
        body = r.json()
        pages += 1
        offset_seen.append(body["page"]["offset"])
        assert body["page"]["total_rows"] == 5
        assert body["page"]["items_per_page"] == 2
        seen.extend(inv["id"] for inv in body["data"])
        cursor = body["page"]["cursor"]
        if not cursor:
            break
    assert pages == 3              # 2 + 2 + 1
    assert offset_seen == [0, 2, 4]
    assert len(seen) == 5 and len(set(seen)) == 5


async def test_invoices_issued_date_window(deel_client, deel_auth):
    # issued_from_date=2026-02-01 drops the January invoices; status=all to see them.
    r = await deel_client.get("/rest/v2/invoices", headers=deel_auth,
                              params={"status": "all", "issued_from_date": "2026-02-01"})
    assert r.status_code == 200
    ids = {inv["id"] for inv in r.json()["data"]}
    assert ids == {"inv_p002", "inv_pend4", "inv_proc5"}, ids


async def test_invalid_cursor_is_400(deel_client, deel_auth):
    r = await deel_client.get("/rest/v2/invoices", headers=deel_auth,
                              params={"cursor": "not-a-real-cursor!"})
    assert r.status_code == 400
    assert isinstance(r.json().get("errors"), list)


async def test_limit_clamped_to_max(deel_client, deel_auth):
    r = await deel_client.get("/rest/v2/invoices", headers=deel_auth,
                              params={"status": "all", "limit": 99999})
    assert r.status_code == 200  # clamped, not an error
    assert r.json()["page"]["items_per_page"] == 100  # clamped to max
    assert r.json()["page"]["cursor"] is None          # all 5 fit


# --------------------------------------------------------- rate limit + errors

async def test_forced_429_with_retry_after(deel_client, deel_auth):
    armed = await deel_client.post("/_control/rate_limit", params={"count": 1})
    assert armed.status_code == 200
    r = await deel_client.get("/rest/v2/contracts", headers=deel_auth)
    assert r.status_code == 429
    assert r.headers.get("Retry-After") == "1"
    assert isinstance(r.json().get("errors"), list)
    # next request recovers
    r2 = await deel_client.get("/rest/v2/contracts", headers=deel_auth)
    assert r2.status_code == 200
