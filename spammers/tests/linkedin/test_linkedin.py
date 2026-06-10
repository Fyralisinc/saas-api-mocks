"""Hard-fail fidelity tests for the LinkedIn mock (REAL api.linkedin.com /rest).

These encode LinkedIn's load-bearing organization Community-Management wire contract
as an audit — every assertion is a fact a consumer that also works against real
LinkedIn depends on:

  * every versioned /rest/ call needs ``Linkedin-Version: YYYYMM`` (missing → 400
    VERSION_MISSING; out-of-window → 426 NONEXISTENT_VERSION) + a Bearer token (missing
    → 401) — the CLASSIC ``{message, serviceErrorCode, status}`` error envelope;
  * ``GET /rest/posts?q=author&author={orgURN}`` is a Rest.li FINDER with OFFSET paging
    (``start``/``count``; envelope ``{elements, paging:{start,count,links}}``; EOF = a
    page with fewer elements than ``count``);
  * post ``id`` is a ``urn:li:share|ugcPost:{n}`` URN; ``createdAt``/``lastModifiedAt``/
    ``publishedAt`` are epoch-MILLIS INTEGERS;
  * the two stats finders (``q=organizationalEntity``) return a single lifetime
    ``elements`` row — ``totalShareStatistics`` (7 counters) / the follower facet
    arrays (``{<segmentKey>, followerCounts:{organicFollowerCount,paidFollowerCount}}``);
  * 429 carries the throttle message and **NO Retry-After / NO X-RateLimit-*** headers.
"""
from __future__ import annotations

import re

import pytest

from .conftest import ACCESS_TOKEN, ORG_ID, ORG_URN, VANITY_NAME, VERSION

pytestmark = pytest.mark.asyncio(loop_scope="session")

_SHARE_FIELDS = {"clickCount", "commentCount", "engagement", "impressionCount",
                 "likeCount", "shareCount", "uniqueImpressionsCount"}


def _posts_params(**extra):
    p = {"q": "author", "author": ORG_URN}
    p.update(extra)
    return p


# ----------------------------------------------------------------- header gates

async def test_missing_version_header_is_400_version_missing(linkedin_client):
    # Bearer present but NO Linkedin-Version → 400 VERSION_MISSING (structurally required).
    r = await linkedin_client.get("/rest/posts", params=_posts_params(),
                                  headers={"Authorization": f"Bearer {ACCESS_TOKEN}"})
    assert r.status_code == 400
    body = r.json()
    assert body["status"] == 400
    assert body["code"] == "VERSION_MISSING"
    assert isinstance(body["message"], str) and body["message"]
    assert "serviceErrorCode" in body


async def test_out_of_window_version_is_426_nonexistent(linkedin_client):
    r = await linkedin_client.get(
        "/rest/posts", params=_posts_params(),
        headers={"Authorization": f"Bearer {ACCESS_TOKEN}", "Linkedin-Version": "202101"})
    assert r.status_code == 426
    body = r.json()
    assert body["status"] == 426
    assert body["code"] == "NONEXISTENT_VERSION"


async def test_malformed_version_is_426(linkedin_client):
    r = await linkedin_client.get(
        "/rest/posts", params=_posts_params(),
        headers={"Authorization": f"Bearer {ACCESS_TOKEN}", "Linkedin-Version": "v2"})
    assert r.status_code == 426


async def test_missing_bearer_is_401_classic_envelope(linkedin_client):
    r = await linkedin_client.get("/rest/posts", params=_posts_params(),
                                  headers={"Linkedin-Version": VERSION})
    assert r.status_code == 401
    body = r.json()
    # the documented classic shape: {message, serviceErrorCode:401, status:401}.
    assert body["status"] == 401
    assert body["serviceErrorCode"] == 401
    assert isinstance(body["message"], str)
    # NOT google.rpc.Status, NOT a Fault.
    assert "code" not in body or isinstance(body.get("code"), str)
    assert "details" not in body and "Fault" not in body


async def test_valid_headers_authorize(linkedin_client, linkedin_headers):
    r = await linkedin_client.get("/rest/posts", params=_posts_params(),
                                  headers=linkedin_headers)
    assert r.status_code == 200


# -------------------------------------------------- organization lookup probe

async def test_get_organization_probe(linkedin_client, linkedin_headers):
    r = await linkedin_client.get(f"/rest/organizations/{ORG_ID}", headers=linkedin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == ORG_ID                 # bare INT id
    assert body["vanityName"] == VANITY_NAME
    assert isinstance(body["localizedName"], str)
    assert isinstance(body["name"], dict) and "localized" in body["name"]


async def test_get_unknown_organization_404(linkedin_client, linkedin_headers):
    r = await linkedin_client.get("/rest/organizations/999999", headers=linkedin_headers)
    assert r.status_code == 404
    assert r.json()["status"] == 404


# ----------------------------------------------------- posts finder + envelope

async def test_posts_finder_requires_author_q(linkedin_client, linkedin_headers):
    # q must be `author`.
    r = await linkedin_client.get("/rest/posts", params={"q": "criteria", "author": ORG_URN},
                                  headers=linkedin_headers)
    assert r.status_code == 400
    # author is required.
    r2 = await linkedin_client.get("/rest/posts", params={"q": "author"},
                                   headers=linkedin_headers)
    assert r2.status_code == 400


async def test_posts_envelope_and_element_shape(linkedin_client, linkedin_headers):
    r = await linkedin_client.get("/rest/posts", params=_posts_params(),
                                  headers=linkedin_headers)
    assert r.status_code == 200
    body = r.json()
    # Rest.li FINDER envelope.
    assert "elements" in body and isinstance(body["elements"], list)
    assert "paging" in body and isinstance(body["paging"], dict)
    assert {"start", "count"} <= set(body["paging"])
    assert isinstance(body["paging"]["links"], list)
    post = body["elements"][0]
    # id is a share/ugcPost URN; author is the org URN.
    assert re.match(r"^urn:li:(share|ugcPost):\d+$", post["id"])
    assert post["author"] == ORG_URN
    assert isinstance(post["commentary"], str)
    assert post["lifecycleState"] == "PUBLISHED"
    assert post["visibility"] == "PUBLIC"
    # timestamps are epoch-MILLIS INTEGERS (not ISO strings).
    for k in ("createdAt", "lastModifiedAt", "publishedAt"):
        assert isinstance(post[k], int) and post[k] > 1_000_000_000_000
    # nested objects.
    assert post["distribution"]["feedDistribution"] == "MAIN_FEED"
    assert post["lifecycleStateInfo"]["isEditedByAuthor"] in (True, False)
    assert isinstance(post["isReshareDisabledByAuthor"], bool)


async def test_posts_offset_paging_three_page_walk(linkedin_client, linkedin_headers):
    # 5 posts at count=2 → pages of 2, 2, 1; the third page (< count) signals EOF.
    seen = []
    start = 0
    pages = 0
    while True:
        r = await linkedin_client.get(
            "/rest/posts", params=_posts_params(start=start, count=2),
            headers=linkedin_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["paging"]["start"] == start
        assert body["paging"]["count"] == 2
        els = body["elements"]
        pages += 1
        seen.extend(e["id"] for e in els)
        if len(els) < 2:
            break
        start += 2
        assert pages < 10
    assert pages == 3
    assert len(seen) == 5
    assert len(set(seen)) == 5            # no overlap across offset pages


async def test_posts_count_caps_at_100(linkedin_client, linkedin_headers):
    r = await linkedin_client.get("/rest/posts", params=_posts_params(count=10000),
                                  headers=linkedin_headers)
    assert r.status_code == 200
    assert r.json()["paging"]["count"] == 100


async def test_posts_unknown_author_returns_empty(linkedin_client, linkedin_headers):
    r = await linkedin_client.get(
        "/rest/posts", params={"q": "author", "author": "urn:li:organization:999"},
        headers=linkedin_headers)
    assert r.status_code == 200
    assert r.json()["elements"] == []


# --------------------------------------------------------- share statistics

async def test_share_statistics_lifetime_shape(linkedin_client, linkedin_headers):
    r = await linkedin_client.get(
        "/rest/organizationalEntityShareStatistics",
        params={"q": "organizationalEntity", "organizationalEntity": ORG_URN},
        headers=linkedin_headers)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["elements"], list) and len(body["elements"]) == 1
    el = body["elements"][0]
    assert el["organizationalEntity"] == ORG_URN
    tss = el["totalShareStatistics"]
    # exactly the seven documented counters (+ engagement is a float).
    assert set(tss) == _SHARE_FIELDS
    assert isinstance(tss["engagement"], float)
    for k in _SHARE_FIELDS - {"engagement"}:
        assert isinstance(tss[k], int)


async def test_share_statistics_requires_org_entity_q(linkedin_client, linkedin_headers):
    r = await linkedin_client.get(
        "/rest/organizationalEntityShareStatistics",
        params={"q": "author", "organizationalEntity": ORG_URN},
        headers=linkedin_headers)
    assert r.status_code == 400


# ------------------------------------------------------- follower statistics

async def test_follower_statistics_facets(linkedin_client, linkedin_headers):
    r = await linkedin_client.get(
        "/rest/organizationalEntityFollowerStatistics",
        params={"q": "organizationalEntity", "organizationalEntity": ORG_URN},
        headers=linkedin_headers)
    assert r.status_code == 200
    el = r.json()["elements"][0]
    assert el["organizationalEntity"] == ORG_URN
    # the seven facet arrays.
    for facet in ("followerCountsByAssociationType", "followerCountsBySeniority",
                  "followerCountsByFunction", "followerCountsByStaffCountRange",
                  "followerCountsByGeoCountry", "followerCountsByGeo",
                  "followerCountsByIndustry"):
        assert facet in el and isinstance(el[facet], list)
    seg = el["followerCountsByAssociationType"][0]
    assert seg["associationType"] in {"EMPLOYEE", "MEMBER"}
    assert set(seg["followerCounts"]) == {"organicFollowerCount", "paidFollowerCount"}
    assert isinstance(seg["followerCounts"]["organicFollowerCount"], int)
    # NO lifetime total on this endpoint (removed — use networkSizes).
    assert "totalFollowerCounts" not in el and "firstDegreeSize" not in el


# ------------------------------------------------------------------- rate limit

async def test_no_ratelimit_headers_on_success(linkedin_client, linkedin_headers):
    r = await linkedin_client.get("/rest/posts", params=_posts_params(),
                                  headers=linkedin_headers)
    assert r.status_code == 200
    # LinkedIn documents NO X-RateLimit-* and NO Retry-After; it emits x-li-* trace headers.
    assert "Retry-After" not in r.headers
    assert not any(k.lower().startswith("x-ratelimit") for k in r.headers)
    assert "x-li-uuid" in r.headers


async def test_forced_429_classic_no_retry_after(linkedin_client, linkedin_headers):
    await linkedin_client.post("/_control/rate_limit", params={"count": 1})
    r = await linkedin_client.get("/rest/posts", params=_posts_params(),
                                  headers=linkedin_headers)
    assert r.status_code == 429
    body = r.json()
    # the 429 body is the CLASSIC envelope with the throttle message.
    assert body["status"] == 429
    assert "throttle" in body["message"].lower()
    assert "Retry-After" not in r.headers
    assert not any(k.lower().startswith("x-ratelimit") for k in r.headers)
    # recovers on the next request.
    r2 = await linkedin_client.get("/rest/posts", params=_posts_params(),
                                   headers=linkedin_headers)
    assert r2.status_code == 200
