"""Hard-fail fidelity tests for the Fireflies mock — the REAL GraphQL contract.

These encode the load-bearing wire facts (pinned from docs.fireflies.ai) and
fail loudly on any divergence:

  * ``POST /graphql`` GraphQL transport: ``{data, errors}`` envelopes, GraphQL
    FIELD SELECTION (only requested fields come back), ``transcripts`` returns a
    plain ``[Transcript]`` array under ``data.transcripts`` (no total/pageInfo).
  * A Transcript's ``date`` is a Float epoch-MILLISECONDS; ``dateString`` is the
    separate ISO-8601 ``…Z`` string; ``duration`` is a Number in MINUTES.
  * ``skip``/``limit`` paging (limit max 50 → ``invalid_arguments``); short page = EOF.
  * ``transcript(id: String!)`` single hydrate; unknown id → ``object_not_found`` (404);
    missing id → ``args_required`` (400).
  * ``user`` (no id) returns the API-key owner — Fireflies' real "verify my token".
  * Auth: missing Bearer → ``auth_failed`` (401).
  * Rate limit: forced 429 = ``too_many_requests`` + ``extensions.metadata.retryAfter``
    (a timestamp) and NO ``Retry-After`` HTTP header.
  * Webhook ``x-hub-signature`` = ``sha256=<hex>`` HMAC-SHA256 over the body.
"""
from __future__ import annotations

import json

import pytest

from spammers.common.signing import fireflies_sign, fireflies_verify

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _gql(client, query, headers, variables=None):
    body = {"query": query}
    if variables is not None:
        body["variables"] = variables
    return await client.post("/graphql", content=json.dumps(body), headers=headers)


# ---------------------------------------------------------------- transport / auth

async def test_missing_bearer_is_auth_failed_401(fireflies_client):
    r = await _gql(fireflies_client, "{ transcripts(limit: 1) { id } }",
                   {"Content-Type": "application/json"})
    assert r.status_code == 401, r.text
    err = r.json()["errors"][0]
    assert err["extensions"]["code"] == "auth_failed"
    assert r.json()["data"] is None


async def test_transcripts_returns_plain_array(fireflies_client, fireflies_headers):
    r = await _gql(fireflies_client, "{ transcripts(limit: 50) { id title } }",
                   fireflies_headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert isinstance(data["transcripts"], list)
    assert len(data["transcripts"]) == 6  # all seeded
    # newest-first ordering: the most recent (10 days ago) leads.
    assert data["transcripts"][0]["id"] == "Fn7QsV4kZp"


# ------------------------------------------------------------ GraphQL field select

async def test_field_selection_returns_only_requested(fireflies_client, fireflies_headers):
    r = await _gql(fireflies_client, "{ transcripts(limit: 1) { id title } }",
                   fireflies_headers)
    t = r.json()["data"]["transcripts"][0]
    assert set(t.keys()) == {"id", "title"}  # GraphQL: nothing else leaks


async def test_nested_field_selection(fireflies_client, fireflies_headers):
    r = await _gql(fireflies_client,
                   "{ transcripts(limit: 1) { id summary { overview meeting_type } } }",
                   fireflies_headers)
    t = r.json()["data"]["transcripts"][0]
    assert set(t.keys()) == {"id", "summary"}
    assert set(t["summary"].keys()) == {"overview", "meeting_type"}


# ----------------------------------------------------------- Transcript field types

async def test_date_is_epoch_millis_and_datestring_is_iso(fireflies_client, fireflies_headers):
    r = await _gql(fireflies_client,
                   "{ transcripts(limit: 1) { id date dateString duration } }",
                   fireflies_headers)
    t = r.json()["data"]["transcripts"][0]
    # date is a NUMBER (epoch milliseconds) — 13 digits, not an ISO string.
    assert isinstance(t["date"], (int, float)) and not isinstance(t["date"], bool)
    assert t["date"] > 1_000_000_000_000  # epoch MS magnitude
    # dateString is the separate ISO-8601 ...Z string with ms precision.
    assert isinstance(t["dateString"], str) and t["dateString"].endswith("Z")
    assert "T" in t["dateString"] and "." in t["dateString"]
    # duration is a Number in MINUTES (our seed uses 30.0 for this newest one).
    assert isinstance(t["duration"], (int, float))
    assert t["duration"] == 30.0


async def test_object_sub_shapes(fireflies_client, fireflies_headers):
    r = await _gql(
        fireflies_client,
        "{ transcripts(limit: 1) { participants meeting_attendees { email displayName } "
        "speakers { id name } meeting_info { summary_status } } }",
        fireflies_headers)
    t = r.json()["data"]["transcripts"][0]
    assert isinstance(t["participants"], list) and isinstance(t["participants"][0], str)
    assert isinstance(t["meeting_attendees"][0]["email"], str)
    assert set(t["meeting_attendees"][0].keys()) == {"email", "displayName"}
    assert t["meeting_info"]["summary_status"] == "processed"


# ------------------------------------------------------------------ skip/limit page

async def test_skip_limit_paging_short_page_eof(fireflies_client, fireflies_headers):
    seen = []
    skip = 0
    for _ in range(10):
        r = await _gql(
            fireflies_client,
            f"{{ transcripts(limit: 2, skip: {skip}) {{ id }} }}", fireflies_headers)
        page = r.json()["data"]["transcripts"]
        seen.extend(p["id"] for p in page)
        if len(page) < 2:   # short page = EOF
            break
        skip += 2
    assert len(seen) == 6
    assert len(set(seen)) == 6  # no dupes across pages


async def test_limit_over_50_is_invalid_arguments(fireflies_client, fireflies_headers):
    r = await _gql(fireflies_client, "{ transcripts(limit: 51) { id } }",
                   fireflies_headers)
    assert r.status_code == 400, r.text
    assert r.json()["errors"][0]["extensions"]["code"] == "invalid_arguments"


async def test_fromdate_todate_filter(fireflies_client, fireflies_headers):
    # only transcripts on/after ~50 days before VNOW (the last two seeded).
    r = await _gql(
        fireflies_client,
        '{ transcripts(limit: 50, fromDate: "2025-12-20T00:00:00.000Z") { id } }',
        fireflies_headers)
    ids = [t["id"] for t in r.json()["data"]["transcripts"]]
    assert "Fn7QsV4kZp" in ids and "Em2YtB9wNs" in ids
    assert "ASxwZxCstx" not in ids  # 200 days ago — filtered out


# -------------------------------------------------------------- transcript(id) query

async def test_single_transcript_hydrate(fireflies_client, fireflies_headers):
    r = await _gql(fireflies_client,
                   '{ transcript(id: "Cz8WnT2hVq") { id title duration } }',
                   fireflies_headers)
    assert r.status_code == 200, r.text
    t = r.json()["data"]["transcript"]
    assert t["id"] == "Cz8WnT2hVq" and t["title"] == "1:1 — Avery / Sam"


async def test_unknown_transcript_is_object_not_found_404(fireflies_client, fireflies_headers):
    r = await _gql(fireflies_client, '{ transcript(id: "DOESNOTEXIST") { id } }',
                   fireflies_headers)
    assert r.status_code == 404, r.text
    assert r.json()["errors"][0]["extensions"]["code"] == "object_not_found"


async def test_transcript_missing_id_is_args_required_400(fireflies_client, fireflies_headers):
    r = await _gql(fireflies_client, "{ transcript { id } }", fireflies_headers)
    assert r.status_code == 400, r.text
    assert r.json()["errors"][0]["extensions"]["code"] == "args_required"


# ------------------------------------------------------------------------ user query

async def test_user_no_id_returns_owner(fireflies_client, fireflies_headers):
    r = await _gql(fireflies_client,
                   "{ user { user_id email name is_admin num_transcripts } }",
                   fireflies_headers)
    assert r.status_code == 200, r.text
    u = r.json()["data"]["user"]
    assert u["user_id"] == "Owner00xYz"
    assert u["email"] == "founder@alpenlabs.io"
    assert u["is_admin"] is True
    assert u["num_transcripts"] == 6


# ------------------------------------------------------------------------ variables

async def test_variables_resolve(fireflies_client, fireflies_headers):
    r = await _gql(
        fireflies_client,
        "query($l: Int!, $s: Int!) { transcripts(limit: $l, skip: $s) { id } }",
        fireflies_headers, variables={"l": 3, "s": 0})
    assert len(r.json()["data"]["transcripts"]) == 3


# ----------------------------------------------------------------------- rate limit

async def test_forced_429_is_too_many_requests_no_retry_after(fireflies_client, fireflies_headers):
    await fireflies_client.post("/_control/rate_limit", params={"count": 1})
    r = await _gql(fireflies_client, "{ transcripts(limit: 1) { id } }", fireflies_headers)
    assert r.status_code == 429, r.text
    err = r.json()["errors"][0]
    assert err["extensions"]["code"] == "too_many_requests"
    # retry hint is a GraphQL extensions.metadata.retryAfter timestamp, NOT a header.
    assert "retryAfter" in err["extensions"]["metadata"]
    assert "Retry-After" not in r.headers
    # the next call (knob spent) succeeds again.
    r2 = await _gql(fireflies_client, "{ transcripts(limit: 1) { id } }", fireflies_headers)
    assert r2.status_code == 200


# -------------------------------------------------------------------------- webhook

async def test_fireflies_sign_is_sha256_hex():
    sig = fireflies_sign("secret", b'{"event":"meeting.transcribed"}')
    assert sig.startswith("sha256=")
    assert len(sig) == len("sha256=") + 64  # hex SHA-256
    assert fireflies_verify("secret", sig, b'{"event":"meeting.transcribed"}')
    assert not fireflies_verify("secret", sig, b'{"event":"tampered"}')
