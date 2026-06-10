"""Mercury mock fidelity suite — hard-fail assertions encoding REAL Mercury wire
behavior (accounts/transactions HTTP API). A red test here is a fidelity gap.

Audited vs Mercury's embedded OpenAPI (docs.mercury.com):
  * Base ``/api/v1``; auth = the org API token via ``Authorization: Bearer`` OR
    ``Basic base64(token:)``; missing/blank -> 401.
  * ``GET /accounts`` -> ``{accounts:[…], page:{nextPage,previousPage}}`` (UUID
    cursor, order default asc, limit default/max 1000). Balances are DOLLARS.
  * ``GET /account/{id}`` -> a BARE Account object (singular ``account``).
  * ``GET /account/{id}/transactions`` -> ``{total:N, transactions:[…]}`` (offset
    pagination, order default desc, status filter, 30-DAY DEFAULT window). Amount
    is signed dollars (negative == debit). status/kind are fixed enums.
  * ``GET /account/{id}/transaction/{id}`` -> a BARE Transaction object.
"""
from __future__ import annotations

import base64

import pytest

from spammers.mercury.dto import ACCOUNT_STATUSES, ACCOUNT_TYPES, TXN_STATUSES, TXN_KINDS
from .conftest import (ACCT_CHECKING, ACCT_SAVINGS, ACCT_TREASURY, API_TOKEN, LEGAL_NAME)

pytestmark = pytest.mark.asyncio(loop_scope="session")

ACC = "/api/v1/accounts"


def _checking_txns(url: str = f"/api/v1/account/{ACCT_CHECKING}/transactions") -> str:
    return url


# ----------------------------------------------------------------------- auth

async def test_missing_auth_is_401(mercury_client):
    r = await mercury_client.get(ACC)
    assert r.status_code == 401
    body = r.json()
    assert "errors" in body
    assert "ok" not in body  # not a slack-shaped error


async def test_bearer_and_basic_auth_both_accepted(mercury_client):
    # Bearer
    r1 = await mercury_client.get(ACC, headers={"Authorization": f"Bearer {API_TOKEN}"})
    assert r1.status_code == 200
    # Basic base64(token:) — the token as the username, empty password
    basic = base64.b64encode(f"{API_TOKEN}:".encode()).decode()
    r2 = await mercury_client.get(ACC, headers={"Authorization": f"Basic {basic}"})
    assert r2.status_code == 200
    assert r2.json()["accounts"]


# ------------------------------------------------------------------- accounts

async def test_accounts_envelope_and_order(mercury_client, mercury_auth):
    r = await mercury_client.get(ACC, headers=mercury_auth)
    assert r.status_code == 200
    body = r.json()
    # NOT a bare array — Mercury wraps accounts + a page cursor object.
    assert isinstance(body, dict)
    assert set(body) >= {"accounts", "page"}
    accts = body["accounts"]
    assert len(accts) == 3
    # default order is ASC by sort key -> checking, savings, treasury
    assert [a["id"] for a in accts] == [str(ACCT_CHECKING), str(ACCT_SAVINGS), str(ACCT_TREASURY)]


async def test_account_object_contract(mercury_client, mercury_auth):
    accts = (await mercury_client.get(ACC, headers=mercury_auth)).json()["accounts"]
    for a in accts:
        for k in ("id", "accountNumber", "routingNumber", "name", "status", "type",
                  "createdAt", "availableBalance", "currentBalance", "kind",
                  "legalBusinessName", "dashboardLink"):
            assert k in a, f"account missing {k}"
        assert a["status"] in ACCOUNT_STATUSES
        assert a["type"] in ACCOUNT_TYPES
        assert a["legalBusinessName"] == LEGAL_NAME
        # createdAt is RFC3339 UTC seconds precision with a Z suffix (no fractional)
        assert a["createdAt"].endswith("Z") and "." not in a["createdAt"]
        # nullable optionals are present (as null), not dropped
        assert "nickname" in a and "canReceiveTransactions" in a


async def test_account_balances_are_dollars_not_cents(mercury_client, mercury_auth):
    accts = (await mercury_client.get(ACC, headers=mercury_auth)).json()["accounts"]
    checking = next(a for a in accts if a["id"] == str(ACCT_CHECKING))
    # 4521900050 cents -> 45219000.50 dollars (a float with cents, not the int cents)
    assert isinstance(checking["currentBalance"], float)
    assert checking["currentBalance"] == 45219000.50
    assert checking["availableBalance"] == 45219000.50


async def test_accounts_cursor_pagination(mercury_client, mercury_auth):
    page1 = (await mercury_client.get(f"{ACC}?limit=1", headers=mercury_auth)).json()
    assert len(page1["accounts"]) == 1
    assert page1["accounts"][0]["id"] == str(ACCT_CHECKING)
    cursor = page1["page"].get("nextPage")
    assert cursor == str(ACCT_CHECKING)  # cursor is an account id (exclusive)
    page2 = (await mercury_client.get(
        f"{ACC}?limit=1&start_after={cursor}", headers=mercury_auth)).json()
    assert page2["accounts"][0]["id"] == str(ACCT_SAVINGS)


async def test_accounts_invalid_order_and_limit_400(mercury_client, mercury_auth):
    assert (await mercury_client.get(f"{ACC}?order=sideways", headers=mercury_auth)).status_code == 400
    assert (await mercury_client.get(f"{ACC}?limit=0", headers=mercury_auth)).status_code == 400
    assert (await mercury_client.get(f"{ACC}?limit=1001", headers=mercury_auth)).status_code == 400


async def test_single_account_is_bare_object(mercury_client, mercury_auth):
    r = await mercury_client.get(f"/api/v1/account/{ACCT_CHECKING}", headers=mercury_auth)
    assert r.status_code == 200
    body = r.json()
    assert "accounts" not in body  # bare, not wrapped
    assert body["id"] == str(ACCT_CHECKING)
    # unknown id -> 404
    r404 = await mercury_client.get(
        "/api/v1/account/99999999-9999-4999-8999-999999999999", headers=mercury_auth)
    assert r404.status_code == 404


# --------------------------------------------------------------- transactions

async def test_transactions_envelope_total_and_offset(mercury_client, mercury_auth):
    url = _checking_txns()
    full = (await mercury_client.get(f"{url}?start=2025-01-01", headers=mercury_auth)).json()
    assert set(full) == {"total", "transactions"}
    assert full["total"] == 5  # 5 checking txns over the wide window
    assert len(full["transactions"]) == 5
    # offset pagination: total stays the full match count, page is a window
    p1 = (await mercury_client.get(f"{url}?start=2025-01-01&limit=2&offset=0", headers=mercury_auth)).json()
    p2 = (await mercury_client.get(f"{url}?start=2025-01-01&limit=2&offset=2", headers=mercury_auth)).json()
    assert p1["total"] == 5 and p2["total"] == 5
    assert len(p1["transactions"]) == 2 and len(p2["transactions"]) == 2
    ids1 = {t["id"] for t in p1["transactions"]}
    ids2 = {t["id"] for t in p2["transactions"]}
    assert ids1.isdisjoint(ids2)


async def test_transactions_default_30day_window(mercury_client, mercury_auth):
    url = _checking_txns()
    # no `start` -> Mercury defaults to the last 30 days: the 2025 txn is excluded
    default = (await mercury_client.get(url, headers=mercury_auth)).json()
    assert default["total"] == 4
    # an explicit wide start reaches the older one
    wide = (await mercury_client.get(f"{url}?start=2024-01-01", headers=mercury_auth)).json()
    assert wide["total"] == 5


async def test_transactions_newest_first(mercury_client, mercury_auth):
    txns = (await mercury_client.get(
        f"{_checking_txns()}?start=2024-01-01", headers=mercury_auth)).json()["transactions"]
    created = [t["createdAt"] for t in txns]
    assert created == sorted(created, reverse=True), "transactions default to newest-first"


async def test_transaction_amount_sign_and_dollars(mercury_client, mercury_auth):
    txns = (await mercury_client.get(
        f"{_checking_txns()}?start=2024-01-01", headers=mercury_auth)).json()["transactions"]
    by_cp = {t["counterpartyName"]: t for t in txns}
    # debit (vendor payment) is negative dollars; credit (incoming wire) positive
    assert by_cp["Amazon Web Services"]["amount"] == -1200.00
    assert by_cp["Paradigm"]["amount"] == 500000.00
    assert isinstance(by_cp["GitHub"]["amount"], float)
    assert by_cp["GitHub"]["amount"] == -45.00


async def test_pending_transaction_has_null_postedat(mercury_client, mercury_auth):
    txns = (await mercury_client.get(
        f"{_checking_txns()}?start=2024-01-01", headers=mercury_auth)).json()["transactions"]
    pending = [t for t in txns if t["status"] == "pending"]
    sent = [t for t in txns if t["status"] == "sent"]
    assert pending and sent
    for t in pending:
        assert t["postedAt"] is None, "a pending transaction has not posted yet"
    for t in sent:
        assert t["postedAt"] is not None and t["postedAt"].endswith("Z")


async def test_transaction_object_contract(mercury_client, mercury_auth):
    txns = (await mercury_client.get(
        f"{_checking_txns()}?start=2024-01-01", headers=mercury_auth)).json()["transactions"]
    for t in txns:
        for k in ("id", "amount", "status", "kind", "createdAt", "estimatedDeliveryDate",
                  "counterpartyId", "counterpartyName", "accountId", "dashboardLink",
                  "compliantWithReceiptPolicy", "hasGeneratedReceipt"):
            assert k in t, f"transaction missing required {k}"
        assert t["status"] in TXN_STATUSES
        assert t["kind"] in TXN_KINDS
        assert t["accountId"] == str(ACCT_CHECKING)
        assert t["estimatedDeliveryDate"] is not None
        assert isinstance(t["compliantWithReceiptPolicy"], bool)
        # the three required arrays are always present arrays
        for arr in ("glAllocations", "attachments", "relatedTransactions"):
            assert isinstance(t[arr], list)


async def test_transactions_status_filter(mercury_client, mercury_auth):
    url = _checking_txns()
    pend = (await mercury_client.get(
        f"{url}?start=2024-01-01&status=pending", headers=mercury_auth)).json()
    assert pend["total"] == 1
    assert all(t["status"] == "pending" for t in pend["transactions"])
    sent = (await mercury_client.get(
        f"{url}?start=2024-01-01&status=sent", headers=mercury_auth)).json()
    assert sent["total"] == 4
    bad = await mercury_client.get(f"{url}?status=bogus", headers=mercury_auth)
    assert bad.status_code == 400


async def test_single_transaction_is_bare_object(mercury_client, mercury_auth):
    txns = (await mercury_client.get(
        f"{_checking_txns()}?start=2024-01-01", headers=mercury_auth)).json()["transactions"]
    tid = txns[0]["id"]
    r = await mercury_client.get(
        f"/api/v1/account/{ACCT_CHECKING}/transaction/{tid}", headers=mercury_auth)
    assert r.status_code == 200
    body = r.json()
    assert "transactions" not in body  # bare, not wrapped
    assert body["id"] == tid
    # unknown txn id -> 404
    r404 = await mercury_client.get(
        f"/api/v1/account/{ACCT_CHECKING}/transaction/00000000-0000-4000-8000-000000000000",
        headers=mercury_auth)
    assert r404.status_code == 404


async def test_transactions_require_auth(mercury_client):
    r = await mercury_client.get(_checking_txns())
    assert r.status_code == 401


# ------------------------------------------------------------ rate-limit control

async def test_forced_429(mercury_client, mercury_auth):
    from spammers.mercury.app import _FORCED_429
    await mercury_client.post("/_control/rate_limit?count=1")
    r = await mercury_client.get(ACC, headers=mercury_auth)
    assert r.status_code == 429
    assert "errors" in r.json()
    _FORCED_429["count"] = 0
    r2 = await mercury_client.get(ACC, headers=mercury_auth)
    assert r2.status_code == 200
