"""Hard-fail fidelity tests for the Carta mock (REAL api.carta.com /v1alpha1).

These encode Carta's load-bearing wire contract as an audit — every assertion is a
fact a consumer that also works against real Carta depends on:

  * OAuth client-credentials mint at POST /o/access_token/ (Basic OR body; the
    response carries NO refresh_token; grant_type is documented UPPERCASE);
  * the issuer cap-table read surface under /v1alpha1/issuers/{id}/… with Google
    AIP-158 token pagination — list endpoints wrap under a PLURAL key alongside
    `nextPageToken`, which is ABSENT on the last page; single GETs wrap under a
    SINGULAR key ({issuer:{…}});
  * MONEY + every decimal/quantity is a PROTOBUF WRAPPER whose `value` is a decimal
    STRING (Money = {currencyCode:{value},amount:{value}}; decimal = {value:"…"});
  * mixed IDs (numeric-string issuer-suite ids vs UUID securityId); RFC3339-µs-Z
    datetimes + date-only dates; NO SyncToken anywhere;
  * google.rpc.Status error envelope ({code,message,details[]}) — EXCEPT the 429
    body which is a FLAT {message} + RateLimit-*/X-RateLimit-*-Second/-Minute
    headers and NO Retry-After.
"""
from __future__ import annotations

import re

import pytest

from .conftest import (ACCESS_TOKEN, CLIENT_ID, CLIENT_SECRET, ISSUER_ID,
                       LEGAL_NAME, SC_COMMON)

pytestmark = pytest.mark.asyncio(loop_scope="session")

_US_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DEC_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _base(issuer: str = ISSUER_ID) -> str:
    return f"/v1alpha1/issuers/{issuer}"


def _is_decimal_wrapper(v) -> bool:
    return isinstance(v, dict) and isinstance(v.get("value"), str) \
        and bool(_DEC_RE.match(v["value"]))


def _is_money(v) -> bool:
    return (isinstance(v, dict)
            and isinstance(v.get("currencyCode"), dict)
            and isinstance(v["currencyCode"].get("value"), str)
            and isinstance(v.get("amount"), dict)
            and isinstance(v["amount"].get("value"), str)
            and bool(_DEC_RE.match(v["amount"]["value"])))


# ----------------------------------------------------------------------- auth

async def test_missing_bearer_is_401_google_rpc_status(carta_client):
    r = await carta_client.get(_base() + "/stakeholders")
    assert r.status_code == 401
    body = r.json()
    # google.rpc.Status (AIP-193): code (int) + message + details[{@type,reason}].
    assert body["code"] == 16
    assert isinstance(body["message"], str)
    assert isinstance(body["details"], list) and body["details"]
    assert body["details"][0]["reason"] == "MISSING_OR_INVALID_ACCESS_TOKEN"
    assert body["details"][0]["@type"].endswith("ErrorInfo")


async def test_valid_bearer_authorizes(carta_client, carta_auth):
    r = await carta_client.get(_base() + "/stakeholders", headers=carta_auth)
    assert r.status_code == 200


# --------------------------------------------------------------- token mint

async def test_token_mint_client_credentials_basic(carta_client):
    import base64
    basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    r = await carta_client.post("/o/access_token/",
                                headers={"Authorization": f"Basic {basic}"},
                                json={"grant_type": "CLIENT_CREDENTIALS",
                                      "scope": "read_issuer_stakeholders"})
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"] == ACCESS_TOKEN
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 3600
    # client-credentials issues NO refresh_token (you re-mint).
    assert "refresh_token" not in body


async def test_token_mint_body_creds(carta_client):
    r = await carta_client.post("/o/access_token/",
                                json={"grant_type": "client_credentials",
                                      "client_id": CLIENT_ID,
                                      "client_secret": CLIENT_SECRET})
    assert r.status_code == 200
    assert r.json()["access_token"] == ACCESS_TOKEN


async def test_token_mint_missing_creds_401(carta_client):
    r = await carta_client.post("/o/access_token/",
                                json={"grant_type": "client_credentials"})
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_client"


# ------------------------------------------------------------------- issuers

async def test_list_issuers_plural_key(carta_client, carta_auth):
    r = await carta_client.get("/v1alpha1/issuers", headers=carta_auth)
    assert r.status_code == 200
    body = r.json()
    assert "issuers" in body and isinstance(body["issuers"], list)
    iss = body["issuers"][0]
    assert iss["id"] == ISSUER_ID          # numeric-string issuer id
    assert iss["legalName"] == LEGAL_NAME
    assert "doingBusinessAsName" in iss and "website" in iss


async def test_get_issuer_singular_wrap(carta_client, carta_auth):
    r = await carta_client.get(_base(), headers=carta_auth)
    assert r.status_code == 200
    body = r.json()
    # single-object GET wraps under the SINGULAR key.
    assert set(body) == {"issuer"}
    assert body["issuer"]["id"] == ISSUER_ID


async def test_unknown_issuer_404(carta_client, carta_auth):
    r = await carta_client.get("/v1alpha1/issuers/999999", headers=carta_auth)
    assert r.status_code == 404
    assert r.json()["code"] == 5


# -------------------------------------------------- stakeholders + AIP paging

async def test_stakeholders_aip_envelope_and_shape(carta_client, carta_auth):
    r = await carta_client.get(_base() + "/stakeholders", headers=carta_auth)
    assert r.status_code == 200
    body = r.json()
    assert "stakeholders" in body and isinstance(body["stakeholders"], list)
    sh = body["stakeholders"][0]
    assert _DEC_RE.match(sh["id"])          # numeric-string id
    assert sh["relationship"] in {"FOUNDER", "EMPLOYEE", "INVESTOR", "BOARD_MEMBER",
                                  "EXECUTIVE", "OTHER"}
    assert sh["entityType"] in {"INDIVIDUAL", "CORPORATION"}
    assert isinstance(sh["address"], dict) and "country" in sh["address"]
    assert "fullName" in sh and "email" in sh


async def test_stakeholders_aip_token_two_page_walk(carta_client, carta_auth):
    # pageSize=2 over 3 stakeholders → page1 carries nextPageToken; page2 omits it.
    r1 = await carta_client.get(_base() + "/stakeholders",
                                params={"pageSize": 2}, headers=carta_auth)
    assert r1.status_code == 200
    b1 = r1.json()
    assert len(b1["stakeholders"]) == 2
    assert "nextPageToken" in b1 and b1["nextPageToken"]
    tok = b1["nextPageToken"]
    r2 = await carta_client.get(_base() + "/stakeholders",
                                params={"pageSize": 2, "pageToken": tok},
                                headers=carta_auth)
    assert r2.status_code == 200
    b2 = r2.json()
    assert len(b2["stakeholders"]) == 1
    # the terminal page MUST omit nextPageToken (the AIP EOF signal).
    assert "nextPageToken" not in b2
    # no overlap across the two pages.
    ids1 = {s["id"] for s in b1["stakeholders"]}
    ids2 = {s["id"] for s in b2["stakeholders"]}
    assert ids1.isdisjoint(ids2)
    assert len(ids1 | ids2) == 3


async def test_pagesize_over_max_is_coerced_not_rejected(carta_client, carta_auth):
    # stakeholders max is 100 — a pageSize of 10000 is coerced down (200, not 400).
    r = await carta_client.get(_base() + "/stakeholders",
                               params={"pageSize": 10000}, headers=carta_auth)
    assert r.status_code == 200


# ---------------------------------------------------------------- share classes

async def test_share_classes_protobuf_wrappers(carta_client, carta_auth):
    r = await carta_client.get(_base() + "/shareClasses", headers=carta_auth)
    assert r.status_code == 200
    body = r.json()
    assert "shareClasses" in body
    classes = {c["type"]: c for c in body["shareClasses"]}
    assert "COMMON" in classes and "PREFERRED" in classes
    common = classes["COMMON"]
    assert common["type"] in {"COMMON", "PREFERRED"}
    # authorizedShareCount is a bare-decimal wrapper {value:"<str>"}.
    assert _is_decimal_wrapper(common["authorizedShareCount"])
    # parValue is a Money wrapper {currencyCode:{value},amount:{value}}.
    assert _is_money(common["parValue"])
    assert isinstance(common["seniority"], int)
    assert isinstance(common["pariPassu"], bool)
    assert isinstance(common["prefix"], str)


# ------------------------------------------------------------------ option grants

async def test_option_grants_shape_and_wrappers(carta_client, carta_auth):
    r = await carta_client.get(_base() + "/optionGrants", headers=carta_auth)
    assert r.status_code == 200
    body = r.json()
    assert "optionGrants" in body
    g = body["optionGrants"][0]
    # mixed ids: numeric-string grant id, UUID securityId.
    assert _DEC_RE.match(g["id"])
    assert re.match(r"^[0-9a-f-]{36}$", g["securityId"])
    assert g["shareClassId"] == SC_COMMON
    assert g["stockOptionType"] in {"ISO", "NSO", "OTHER"}
    # quantities are decimal wrappers; exercisePrice is Money.
    assert _is_decimal_wrapper(g["quantity"])
    assert _is_decimal_wrapper(g["vestedQuantity"])
    assert _is_money(g["exercisePrice"])
    assert isinstance(g["earlyExercisable"], bool)
    # dates are date-only; lastModifiedDatetime is RFC3339-µs-Z.
    assert _DATE_RE.match(g["issueDate"])
    assert _US_Z_RE.match(g["lastModifiedDatetime"])
    # NO SyncToken (a QBO carryover the Fyralis client expects), NO grant-level status.
    assert "syncToken" not in g and "SyncToken" not in g
    assert "status" not in g


async def test_option_grants_last_modified_filter(carta_client, carta_auth):
    # lastModifiedDatetimeAfter far in the future → empty page (filter is honoured).
    r = await carta_client.get(
        _base() + "/optionGrants",
        params={"lastModifiedDatetimeAfter": "2099-01-01T00:00:00Z"},
        headers=carta_auth)
    assert r.status_code == 200
    assert r.json()["optionGrants"] == []
    # a bad value → 400 google.rpc.Status INVALID_ARGUMENT.
    rb = await carta_client.get(_base() + "/optionGrants",
                                params={"lastModifiedDatetimeAfter": "not-a-date"},
                                headers=carta_auth)
    assert rb.status_code == 400
    assert rb.json()["code"] == 3


# --------------------------------------------------------------- convertible notes

async def test_convertible_notes_safe_shape(carta_client, carta_auth):
    r = await carta_client.get(_base() + "/convertibleNotes", headers=carta_auth)
    assert r.status_code == 200
    body = r.json()
    assert "convertibleNotes" in body
    n = body["convertibleNotes"][0]
    assert n["securityLabel"].startswith("SAFE-")
    assert _is_money(n["cashPaid"])
    assert _is_money(n["priceCap"])
    assert _is_decimal_wrapper(n["interestRate"])
    assert _is_decimal_wrapper(n["discountPercentage"])
    assert n["interestCompoundingPeriod"] in {"SIMPLE", "DAILY", "MONTHLY",
                                              "SEMI_ANNUALLY", "ANNUALLY"}
    assert n["dayCountBasis"] in {"COUNT_30_360", "COUNT_ACTUAL_360", "COUNT_ACTUAL_365"}
    assert _US_Z_RE.match(n["issueDatetime"])
    assert re.match(r"^[0-9a-f-]{36}$", n["securityId"])


# ------------------------------------------------------------------- rate limit

async def test_rate_limit_headers_present_on_success(carta_client, carta_auth):
    r = await carta_client.get(_base() + "/stakeholders", headers=carta_auth)
    assert r.status_code == 200
    # Carta's split per-second/per-minute headers, NO Retry-After.
    assert "RateLimit-Reset" in r.headers
    assert "X-RateLimit-Limit-Second" in r.headers
    assert "X-RateLimit-Limit-Minute" in r.headers
    assert "Retry-After" not in r.headers


async def test_forced_429_is_flat_message_no_retry_after(carta_client, carta_auth):
    await carta_client.post("/_control/rate_limit", params={"count": 1})
    r = await carta_client.get(_base() + "/stakeholders", headers=carta_auth)
    assert r.status_code == 429
    body = r.json()
    # the 429 body is a FLAT {message} — NOT the google.rpc.Status envelope.
    assert body == {"message": "API rate limit exceeded"}
    assert "code" not in body and "details" not in body
    assert "RateLimit-Reset" in r.headers
    assert "Retry-After" not in r.headers
    # recovers on the next request.
    r2 = await carta_client.get(_base() + "/stakeholders", headers=carta_auth)
    assert r2.status_code == 200
