"""QuickBooks Online mock — contract + behavior fidelity (QBO Accounting API v3).

Encodes the real QBO behavior Fyralis relies on: OAuth Bearer auth, the SQL
``query`` endpoint serving Invoice/Bill/BillPayment/Payment in the
``QueryResponse.{Entity}`` envelope with 1-based STARTPOSITION/MAXRESULTS, the
``Metadata.LastUpdatedTime`` incremental filter, ``WHERE Id`` fetch-back, COUNT(*),
CompanyInfo, Fault errors, the 429 ThrottleExceeded path, and the
``intuit-signature`` (base64 HMAC-SHA256) webhook scheme.
"""
from __future__ import annotations

from urllib.parse import quote

import pytest

from spammers.common.signing import intuit_sign, intuit_verify
from spammers.tests.quickbooks.conftest import ACCESS_TOKEN, REALM_ID, auth_header

pytestmark = pytest.mark.asyncio(loop_scope="session")

_BASE = f"/v3/company/{REALM_ID}"


def _q(sql: str) -> str:
    return f"{_BASE}/query?query={quote(sql)}&minorversion=75"


# ---- auth -----------------------------------------------------------------

async def test_no_bearer_is_401_fault(qb_client):
    r = await qb_client.get(_q("SELECT * FROM Bill"))
    assert r.status_code == 401
    body = r.json()
    assert body["Fault"]["type"] and body["Fault"]["Error"][0]["code"] == "3200"


# ---- query envelope + the four entities -----------------------------------

async def test_query_bill_envelope_and_shape(qb_client, qb_auth):
    r = await qb_client.get(_q("SELECT * FROM Bill STARTPOSITION 1 MAXRESULTS 10"), headers=qb_auth)
    assert r.status_code == 200
    body = r.json()
    assert "time" in body  # top-level sibling of QueryResponse
    qr = body["QueryResponse"]
    assert qr["startPosition"] == 1
    bills = qr["Bill"]
    assert len(bills) == 2 and qr["maxResults"] == 2
    b = bills[0]
    assert b["Id"] and b["SyncToken"] == "0" and b["domain"] == "QBO"
    assert b["VendorRef"]["name"] in ("Justworks", "AWS")
    assert b["APAccountRef"]["value"] == "2000"
    line = b["Line"][0]
    assert line["DetailType"] == "AccountBasedExpenseLineDetail"
    assert line["AccountBasedExpenseLineDetail"]["AccountRef"]["value"] in ("5000", "5310")
    assert b["TotalAmt"] in (1800.0, 4200.0)
    assert b["MetaData"]["LastUpdatedTime"]


async def test_query_billpayment_links_to_bill(qb_client, qb_auth):
    r = await qb_client.get(_q("SELECT * FROM BillPayment"), headers=qb_auth)
    bps = r.json()["QueryResponse"]["BillPayment"]
    assert len(bps) == 2
    bp = bps[0]
    assert bp["Id"].startswith("BP-")
    assert bp["PayType"] == "Check"
    assert bp["CheckPayment"]["BankAccountRef"]["value"] == "1000"
    linked = bp["Line"][0]["LinkedTxn"][0]
    assert linked["TxnType"] == "Bill" and linked["TxnId"] == bp["Id"].removeprefix("BP-")


async def test_query_invoice_shape(qb_client, qb_auth):
    r = await qb_client.get(_q("SELECT * FROM Invoice"), headers=qb_auth)
    invs = r.json()["QueryResponse"]["Invoice"]
    assert len(invs) == 1
    inv = invs[0]
    assert inv["CustomerRef"]["name"] == "Starknet Foundation"
    assert inv["Line"][0]["DetailType"] == "SalesItemLineDetail"
    assert inv["TotalAmt"] == 250000.0 and inv["DocNumber"].startswith("INV-")


async def test_query_payment_links_to_invoice(qb_client, qb_auth):
    r = await qb_client.get(_q("SELECT * FROM Payment"), headers=qb_auth)
    pays = r.json()["QueryResponse"]["Payment"]
    assert len(pays) == 1
    p = pays[0]
    assert p["Id"].startswith("P-") and p["UnappliedAmt"] == 0.0
    assert p["Line"][0]["LinkedTxn"][0]["TxnType"] == "Invoice"
    assert p["DepositToAccountRef"]["value"] == "1000"


# ---- pagination + filters -------------------------------------------------

async def test_pagination_startposition(qb_client, qb_auth):
    r1 = await qb_client.get(_q("SELECT * FROM Bill STARTPOSITION 1 MAXRESULTS 1"), headers=qb_auth)
    r2 = await qb_client.get(_q("SELECT * FROM Bill STARTPOSITION 2 MAXRESULTS 1"), headers=qb_auth)
    b1 = r1.json()["QueryResponse"]
    b2 = r2.json()["QueryResponse"]
    assert len(b1["Bill"]) == 1 and b1["startPosition"] == 1
    assert len(b2["Bill"]) == 1 and b2["startPosition"] == 2
    assert b1["Bill"][0]["Id"] != b2["Bill"][0]["Id"]


async def test_where_id_fetchback(qb_client, qb_auth):
    bills = (await qb_client.get(_q("SELECT * FROM Bill MAXRESULTS 1"), headers=qb_auth)
             ).json()["QueryResponse"]["Bill"]
    bid = bills[0]["Id"]
    r = await qb_client.get(_q(f"SELECT * FROM Bill WHERE Id = '{bid}'"), headers=qb_auth)
    got = r.json()["QueryResponse"]["Bill"]
    assert len(got) == 1 and got[0]["Id"] == bid


async def test_incremental_lastupdated_filter(qb_client, qb_auth):
    # Both purchases are at/after 2026-01-10 09:00; a floor past the first excludes it.
    r = await qb_client.get(
        _q("SELECT * FROM Bill WHERE Metadata.LastUpdatedTime > '2026-01-10T10:00:00+00:00'"),
        headers=qb_auth)
    bills = r.json()["QueryResponse"]["Bill"]
    assert len(bills) == 1  # only the 2h-offset purchase


async def test_count_star(qb_client, qb_auth):
    r = await qb_client.get(_q("SELECT COUNT(*) FROM Bill"), headers=qb_auth)
    body = r.json()
    assert body["QueryResponse"]["totalCount"] == 2
    assert "Bill" not in body["QueryResponse"]


# ---- companyinfo + errors -------------------------------------------------

async def test_companyinfo(qb_client, qb_auth):
    r = await qb_client.get(f"{_BASE}/companyinfo/{REALM_ID}", headers=qb_auth)
    assert r.status_code == 200
    ci = r.json()["CompanyInfo"]
    assert ci["CompanyName"] == "Alpen Labs" and ci["Id"] == REALM_ID


async def test_unknown_entity_is_400_fault(qb_client, qb_auth):
    r = await qb_client.get(_q("SELECT * FROM Customer"), headers=qb_auth)
    assert r.status_code == 400
    assert r.json()["Fault"]["type"] == "ValidationFault"


async def test_unknown_realm_is_404_fault(qb_client, qb_auth):
    r = await qb_client.get("/v3/company/0000/query?query=SELECT+*+FROM+Bill", headers=qb_auth)
    assert r.status_code == 404
    assert r.json()["Fault"]["Error"][0]["code"] == "610"


# ---- rate limiting --------------------------------------------------------

async def test_rate_limit_429_throttle(qb_client, qb_auth):
    await qb_client.post(f"{_BASE.replace('/v3/company/' + REALM_ID, '')}/_control/rate_limit?count=1")
    r = await qb_client.get(_q("SELECT * FROM Bill"), headers=qb_auth)
    assert r.status_code == 429
    fault = r.json()["Fault"]
    assert fault["type"] == "THROTTLE" and fault["Error"][0]["code"] == "003001"
    # next call recovers
    r2 = await qb_client.get(_q("SELECT * FROM Bill"), headers=qb_auth)
    assert r2.status_code == 200


# ---- webhook signature (intuit-signature = base64 HMAC-SHA256) -------------

async def test_intuit_signature_is_base64_hmac():
    body = b'{"eventNotifications":[{"realmId":"x"}]}'
    sig = intuit_sign("verifier-token", body)
    # base64, not hex: contains no 'sha256=' prefix and decodes to 32 bytes.
    import base64
    assert "=" in sig or len(sig) >= 43
    assert len(base64.b64decode(sig)) == 32
    assert intuit_verify("verifier-token", sig, body)
    assert not intuit_verify("verifier-token", sig, body + b"x")
