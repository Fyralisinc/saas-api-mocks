"""Hard-fail fidelity tests for the Ramp mock (the REAL api.ramp.com /developer/v1).

These encode the wire facts pinned from Ramp's official OpenAPI + guides and FAIL
on any divergence — the suite is an audit, not a smoke test. Highest-risk facts:
KEYSET pagination ({data, page:{next: full-URL with start=}}), DUAL money
(top-level dollars NUMBER + nested CurrencyAmount integer cents), `currency_code`
(txn) vs `currency` (reimbursement), ISO-8601 +00:00 offset timestamps, the
client-credentials token mint, and 429 WITHOUT Retry-After.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

import pytest

from spammers.ramp.dto import (TRANSACTION_STATES, SYNC_STATUSES, REIMBURSEMENT_TYPES,
                               CARD_STATES)
from .conftest import (ACCESS_TOKEN, CLIENT_ID, CLIENT_SECRET, BUSINESS_ID,
                       USER_A, CARD_A, TXNS, REIMBS)

pytestmark = pytest.mark.asyncio(loop_scope="session")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_OFFSET_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")


def _is_currency_amount(m):
    return (isinstance(m, dict) and isinstance(m.get("amount"), int)
            and not isinstance(m.get("amount"), bool)
            and isinstance(m.get("currency_code"), str))


# --------------------------------------------------------------------- auth

async def test_missing_auth_is_401_error_v2_envelope(ramp_client):
    r = await ramp_client.get("/developer/v1/transactions")
    assert r.status_code == 401
    body = r.json()
    assert "error_v2" in body and "error_code" in body["error_v2"]
    assert "message" in body


async def test_bearer_auth_ok(ramp_client, ramp_auth):
    r = await ramp_client.get("/developer/v1/transactions", headers=ramp_auth)
    assert r.status_code == 200


async def test_x_trace_id_header_present(ramp_client, ramp_auth):
    r = await ramp_client.get("/developer/v1/transactions", headers=ramp_auth)
    assert r.headers.get("x-trace-id"), "every Ramp response carries an x-trace-id"


# ------------------------------------------------------------- token endpoint

async def test_token_mint_client_credentials(ramp_client):
    import base64
    basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    r = await ramp_client.post("/developer/v1/token",
                               headers={"Authorization": f"Basic {basic}"},
                               json={"grant_type": "client_credentials",
                                     "scope": "transactions:read"})
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"] == ACCESS_TOKEN
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 864000  # client-credentials tokens live 10 days
    assert "transactions:read" in body["scope"]


async def test_token_missing_creds_is_401_invalid_client(ramp_client):
    r = await ramp_client.post("/developer/v1/token",
                               json={"grant_type": "client_credentials"})
    assert r.status_code == 401
    assert r.json()["error_v2"]["error_code"] == "invalid_client"


# ------------------------------------------------------------- transactions

async def test_transactions_envelope_and_default_page_size(ramp_client, ramp_auth):
    r = await ramp_client.get("/developer/v1/transactions", headers=ramp_auth)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"data", "page"}, sorted(body)
    assert set(body["page"]) == {"next"}
    assert isinstance(body["data"], list) and len(body["data"]) == 4  # < default 20
    assert body["page"]["next"] is None  # all fit in one page


async def test_transaction_dual_money_dollars_and_cents(ramp_client, ramp_auth):
    r = await ramp_client.get("/developer/v1/transactions", headers=ramp_auth)
    by_id = {t["id"]: t for t in r.json()["data"]}
    t = by_id[TXNS[0][0]]  # 12_000_000 cents == $120,000.00
    # top-level `amount` is a NUMBER in dollars
    assert t["amount"] == 120000.0 and isinstance(t["amount"], float)
    assert t["currency_code"] == "USD"
    # nested CurrencyAmount is integer CENTS
    ca = t["original_transaction_amount"]
    assert _is_currency_amount(ca) and ca["amount"] == 12_000_000
    assert ca["minor_unit_conversion_rate"] == 100
    # line_items[].amount is also a CurrencyAmount (cents)
    assert _is_currency_amount(t["line_items"][0]["amount"])


async def test_transaction_shape_enums_and_offset_timestamps(ramp_client, ramp_auth):
    r = await ramp_client.get("/developer/v1/transactions", headers=ramp_auth)
    for t in r.json()["data"]:
        assert t["state"] in TRANSACTION_STATES, t["state"]
        assert t["sync_status"] in SYNC_STATUSES, t["sync_status"]
        # ISO-8601 with a +00:00 OFFSET (not Z)
        assert _OFFSET_TS_RE.match(t["user_transaction_time"]), t["user_transaction_time"]
        assert _OFFSET_TS_RE.match(t["settlement_date"]), t["settlement_date"]
        assert isinstance(t["card_holder"], dict) and "user_id" in t["card_holder"]


async def test_transactions_keyset_cursor_walk(ramp_client, ramp_auth):
    """2-page keyset walk at page_size=2: page.next is a FULL URL with start=<last id>,
    null at EOF; complete + in sort order."""
    seen, url, pages = [], "/developer/v1/transactions?page_size=2", 0
    while pages < 10:
        r = await ramp_client.get(url, headers=ramp_auth)
        assert r.status_code == 200
        body = r.json()
        pages += 1
        for t in body["data"]:
            seen.append(t["id"])
        nxt = body["page"]["next"]
        if nxt is None:
            break
        # page.next is a FULL URL embedding start=<last entity id of this page>
        parts = urlsplit(nxt)
        assert parts.path == "/developer/v1/transactions"
        q = parse_qs(parts.query)
        assert q["start"] == [body["data"][-1]["id"]]
        url = f"{parts.path}?{parts.query}"
    assert pages == 2
    assert seen == [t[0] for t in TXNS]


async def test_single_transaction_is_bare_object_not_wrapped(ramp_client, ramp_auth):
    tid = TXNS[1][0]
    r = await ramp_client.get(f"/developer/v1/transactions/{tid}", headers=ramp_auth)
    assert r.status_code == 200
    body = r.json()
    assert "data" not in body and "page" not in body  # BARE object, not {data:…}
    assert body["id"] == tid


async def test_transaction_unknown_id_is_404(ramp_client, ramp_auth):
    r = await ramp_client.get("/developer/v1/transactions/nope-nope", headers=ramp_auth)
    assert r.status_code == 404
    assert "error_v2" in r.json()


async def test_transactions_state_filter(ramp_client, ramp_auth):
    r = await ramp_client.get("/developer/v1/transactions", headers=ramp_auth,
                              params={"state": "DECLINED"})
    assert r.status_code == 200
    ids = {t["id"] for t in r.json()["data"]}
    assert ids == {TXNS[3][0]}  # the lone DECLINED


# ----------------------------------------------------------- reimbursements

async def test_reimbursements_currency_key_and_date_only(ramp_client, ramp_auth):
    r = await ramp_client.get("/developer/v1/reimbursements", headers=ramp_auth)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"data", "page"}
    assert len(body["data"]) == 2
    for rb in body["data"]:
        # reimbursement keys `currency` (NOT `currency_code` — the API quirk)
        assert "currency" in rb and "currency_code" not in rb
        assert rb["type"] in REIMBURSEMENT_TYPES, rb["type"]
        assert rb["direction"] == "BUSINESS_TO_USER"
        # transaction_date is DATE-only
        assert _DATE_RE.match(rb["transaction_date"]), rb["transaction_date"]
        # amount = dollars number; original_reimbursement_amount = cents CurrencyAmount
        assert isinstance(rb["amount"], float)
        assert _is_currency_amount(rb["original_reimbursement_amount"])


# ------------------------------------------------------------------- cards

async def test_cards_envelope_and_shape(ramp_client, ramp_auth):
    r = await ramp_client.get("/developer/v1/cards", headers=ramp_auth)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"data", "page"}
    assert len(body["data"]) == 1
    c = body["data"][0]
    assert c["id"] == CARD_A and c["state"] in CARD_STATES
    assert c["cardholder_id"] == USER_A
    assert isinstance(c["is_physical"], bool) and isinstance(c["last_four"], str)


# ------------------------------------------------------------------- users

async def test_users_envelope_and_shape(ramp_client, ramp_auth):
    r = await ramp_client.get("/developer/v1/users", headers=ramp_auth)
    assert r.status_code == 200
    body = r.json()
    assert len(body["data"]) == 3
    by_id = {u["id"]: u for u in body["data"]}
    alice = by_id[USER_A]
    assert alice["email"] == "alice@alpenlabs.com"
    assert alice["is_manager"] is True
    assert alice["business_id"] == BUSINESS_ID


# ----------------------------------------------- pagination / param errors

async def test_invalid_start_cursor_is_400(ramp_client, ramp_auth):
    r = await ramp_client.get("/developer/v1/transactions", headers=ramp_auth,
                              params={"start": "not-a-real-id"})
    assert r.status_code == 400
    assert "error_v2" in r.json()


async def test_page_size_clamped_to_max(ramp_client, ramp_auth):
    r = await ramp_client.get("/developer/v1/transactions", headers=ramp_auth,
                              params={"page_size": 99999})
    assert r.status_code == 200  # clamped to 100, not an error
    assert r.json()["page"]["next"] is None  # all 4 fit


async def test_invalid_page_size_is_400(ramp_client, ramp_auth):
    r = await ramp_client.get("/developer/v1/transactions", headers=ramp_auth,
                              params={"page_size": "abc"})
    assert r.status_code == 400


# --------------------------------------------------------- rate limit + errors

async def test_forced_429_has_no_retry_after(ramp_client, ramp_auth):
    armed = await ramp_client.post("/_control/rate_limit", params={"count": 1})
    assert armed.status_code == 200
    r = await ramp_client.get("/developer/v1/transactions", headers=ramp_auth)
    assert r.status_code == 429
    # Ramp publishes NO Retry-After / X-RateLimit-* headers (real divergence from
    # the Brex/QBO archetype — only client-side backoff is documented).
    assert "Retry-After" not in r.headers
    assert not any(h.lower().startswith("x-ratelimit") for h in r.headers)
    assert r.json()["error_v2"]["error_code"] == "rate_limit_exceeded"
    # next request recovers
    r2 = await ramp_client.get("/developer/v1/transactions", headers=ramp_auth)
    assert r2.status_code == 200
