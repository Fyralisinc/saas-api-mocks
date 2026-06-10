"""Hard-fail fidelity tests for the Brex mock (the REAL api.brex.com /v2/ contract).

These encode the wire facts pinned from Brex's official OpenAPI and FAIL on any
divergence — the suite is an audit, not a smoke test. Highest-risk facts:
cursor pagination ({next_cursor, items}), Money = SIGNED INTEGER CENTS object,
DATE-only transaction dates, cash-vs-card account/txn shape split, Bearer-only
auth, and the {errors:{type,message}} envelope.
"""
from __future__ import annotations

import re

import pytest

from spammers.brex.dto import CASH_TXN_TYPES, CARD_TXN_TYPES
from .conftest import API_TOKEN, CASH_PRIMARY, CASH_SECONDARY, CARD_PRIMARY

pytestmark = pytest.mark.asyncio(loop_scope="session")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_money(m, *, nullable_ok=False):
    if m is None:
        return nullable_ok
    return (isinstance(m, dict) and isinstance(m.get("amount"), int)
            and not isinstance(m.get("amount"), bool)
            and isinstance(m.get("currency"), str))


# --------------------------------------------------------------------- auth

async def test_missing_auth_is_401(brex_client):
    r = await brex_client.get("/v2/accounts/cash")
    assert r.status_code == 401
    body = r.json()
    assert "errors" in body and "type" in body["errors"] and "message" in body["errors"]


async def test_basic_auth_is_not_a_brex_scheme(brex_client):
    # Brex is Bearer-only (unlike mercury/ashby); a Basic header is unauthenticated.
    import base64
    tok = base64.b64encode(f"{API_TOKEN}:".encode()).decode()
    r = await brex_client.get("/v2/accounts/cash", headers={"Authorization": f"Basic {tok}"})
    assert r.status_code == 401


async def test_bearer_auth_ok(brex_client, brex_auth):
    r = await brex_client.get("/v2/accounts/cash", headers=brex_auth)
    assert r.status_code == 200


# ----------------------------------------------------------- cash accounts

async def test_cash_accounts_envelope_and_shape(brex_client, brex_auth):
    r = await brex_client.get("/v2/accounts/cash", headers=brex_auth)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"next_cursor", "items"}, f"envelope keys {sorted(body)}"
    assert isinstance(body["items"], list) and len(body["items"]) == 2
    primaries = 0
    for a in body["items"]:
        assert set(a) == {"id", "name", "status", "current_balance", "available_balance",
                          "account_number", "routing_number", "primary"}, sorted(a)
        assert a["status"] == "ACTIVE"
        assert _is_money(a["current_balance"]) and _is_money(a["available_balance"])
        assert isinstance(a["account_number"], str) and isinstance(a["routing_number"], str)
        assert isinstance(a["primary"], bool)
        primaries += int(a["primary"])
    assert primaries == 1, "exactly one cash account is primary"


async def test_cash_balance_is_integer_cents_not_dollars(brex_client, brex_auth):
    r = await brex_client.get(f"/v2/accounts/cash/{CASH_PRIMARY}", headers=brex_auth)
    assert r.status_code == 200
    bal = r.json()["current_balance"]
    # Seeded 246538500 cents == $2,465,385.00. A dollars projection would be 2465385.0.
    assert bal["amount"] == 246538500 and isinstance(bal["amount"], int)
    assert bal["currency"] == "USD"


async def test_cash_primary_selector(brex_client, brex_auth):
    r = await brex_client.get("/v2/accounts/cash/primary", headers=brex_auth)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == CASH_PRIMARY and body["primary"] is True


async def test_cash_account_single_bare_object(brex_client, brex_auth):
    r = await brex_client.get(f"/v2/accounts/cash/{CASH_SECONDARY}", headers=brex_auth)
    assert r.status_code == 200
    assert r.json()["id"] == CASH_SECONDARY and r.json()["primary"] is False


async def test_cash_account_unknown_is_404(brex_client, brex_auth):
    r = await brex_client.get("/v2/accounts/cash/dpsa_nope", headers=brex_auth)
    assert r.status_code == 404
    assert "errors" in r.json()


# ----------------------------------------------------------- card accounts

async def test_card_accounts_is_bare_array_not_envelope(brex_client, brex_auth):
    r = await brex_client.get("/v2/accounts/card", headers=brex_auth)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list), "GET /v2/accounts/card is a BARE ARRAY (no pagination)"
    assert len(body) == 1
    c = body[0]
    assert set(c) == {"id", "status", "current_balance", "available_balance",
                      "account_limit", "current_statement_period"}, sorted(c)
    assert c["id"] == CARD_PRIMARY and c["status"] == "ACTIVE"
    assert _is_money(c["current_balance"]) and _is_money(c["account_limit"])
    period = c["current_statement_period"]
    assert _DATE_RE.match(period["start_date"]) and _DATE_RE.match(period["end_date"])


# ----------------------------------------------------- cash transactions

async def test_cash_transactions_shape_and_dates(brex_client, brex_auth):
    r = await brex_client.get(f"/v2/transactions/cash/{CASH_PRIMARY}", headers=brex_auth)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"next_cursor", "items"}
    items = body["items"]
    assert len(items) == 4
    for t in items:
        assert set(t) == {"id", "description", "amount", "initiated_at_date",
                          "posted_at_date", "type", "transfer_id"}, sorted(t)
        assert _is_money(t["amount"], nullable_ok=True)
        # DATE-ONLY YYYY-MM-DD (no time component)
        assert _DATE_RE.match(t["initiated_at_date"]), t["initiated_at_date"]
        assert _DATE_RE.match(t["posted_at_date"]), t["posted_at_date"]
        assert t["type"] in CASH_TXN_TYPES, t["type"]


async def test_cash_amount_is_signed(brex_client, brex_auth):
    r = await brex_client.get(f"/v2/transactions/cash/{CASH_PRIMARY}", headers=brex_auth)
    by_id = {t["id"]: t for t in r.json()["items"]}
    assert by_id["txn_cash01"]["amount"]["amount"] == 250_000_000   # credit (positive)
    assert by_id["txn_cash02"]["amount"]["amount"] == -1_500_000    # debit (negative)


async def test_cash_transactions_cursor_walk(brex_client, brex_auth):
    """Two-page cursor walk at limit=2: next_cursor present then null; complete + ascending."""
    seen, cursor, pages = [], None, 0
    prev_posted = None
    while pages < 10:
        params = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        r = await brex_client.get(f"/v2/transactions/cash/{CASH_PRIMARY}",
                                  headers=brex_auth, params=params)
        assert r.status_code == 200
        body = r.json()
        pages += 1
        for t in body["items"]:
            seen.append(t["id"])
            if prev_posted is not None:
                assert t["posted_at_date"] >= prev_posted, "ascending by posted_at"
            prev_posted = t["posted_at_date"]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert pages == 2
    assert seen == ["txn_cash01", "txn_cash02", "txn_cash03", "txn_cash04"]


async def test_cash_posted_at_start_filter(brex_client, brex_auth):
    # VNOW - 19d lands between cash03 (18d) and cash02 (20d): returns cash03 + cash04.
    r = await brex_client.get(f"/v2/transactions/cash/{CASH_PRIMARY}", headers=brex_auth,
                              params={"posted_at_start": "2026-01-13T00:00:00Z"})
    assert r.status_code == 200
    ids = {t["id"] for t in r.json()["items"]}
    assert ids == {"txn_cash03", "txn_cash04"}, ids


async def test_invalid_cursor_is_400(brex_client, brex_auth):
    r = await brex_client.get(f"/v2/transactions/cash/{CASH_PRIMARY}", headers=brex_auth,
                              params={"cursor": "not-a-real-cursor!"})
    assert r.status_code == 400
    assert "errors" in r.json()


async def test_limit_clamped_to_max(brex_client, brex_auth):
    r = await brex_client.get(f"/v2/transactions/cash/{CASH_PRIMARY}", headers=brex_auth,
                              params={"limit": 99999})
    assert r.status_code == 200  # clamped to 1000, not an error
    assert r.json()["next_cursor"] is None  # all 4 fit


# ----------------------------------------------------- card transactions

async def test_card_transactions_shape(brex_client, brex_auth):
    r = await brex_client.get("/v2/transactions/card/primary", headers=brex_auth)
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 4
    for t in items:
        assert set(t) == {"id", "card_id", "description", "amount", "initiated_at_date",
                          "posted_at_date", "type", "merchant", "expense_id"}, sorted(t)
        assert _is_money(t["amount"])  # required & non-null on card
        assert t["type"] in CARD_TXN_TYPES, t["type"]
        assert _DATE_RE.match(t["posted_at_date"])
        m = t["merchant"]
        assert set(m) == {"raw_descriptor", "mcc", "country"}, sorted(m)
        assert m["country"] == "USA"


async def test_card_amount_signs(brex_client, brex_auth):
    r = await brex_client.get("/v2/transactions/card/primary", headers=brex_auth)
    by_id = {t["id"]: t for t in r.json()["items"]}
    assert by_id["txn_card01"]["amount"]["amount"] == 120_000   # PURCHASE positive (charge)
    assert by_id["txn_card04"]["amount"]["amount"] == -12_000   # REFUND negative (credit)


# --------------------------------------------------------- rate limit + errors

async def test_forced_429_with_retry_after(brex_client, brex_auth):
    armed = await brex_client.post("/_control/rate_limit", params={"count": 1})
    assert armed.status_code == 200
    r = await brex_client.get("/v2/accounts/cash", headers=brex_auth)
    assert r.status_code == 429
    assert r.headers.get("Retry-After") == "1"
    assert r.json()["errors"]["type"] == "RATE_LIMITED"
    # next request recovers
    r2 = await brex_client.get("/v2/accounts/cash", headers=brex_auth)
    assert r2.status_code == 200
