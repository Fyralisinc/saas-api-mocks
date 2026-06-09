"""Google Calendar mock — contract + behavior fidelity.

Encodes the real Calendar v3 behavior the consumer's poller relies on: DWD
token exchange, the three events.list modes, 410 on an expired syncToken, the
event resource shape, and per-user 429s.
"""
from __future__ import annotations

import urllib.parse

import jwt
import pytest

from spammers.calendar.tokens import encode_sync_token
from spammers.tests.calendar.conftest import ALICE, BOB, SCOPE, cal_token

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ---- auth -----------------------------------------------------------------

async def test_token_exchange(cal_client):
    assertion = jwt.encode({"iss": "sa", "sub": ALICE, "scope": SCOPE}, "k" * 32, algorithm="HS256")
    r = await cal_client.post("/token", data={
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "Bearer"
    assert body["access_token"].startswith("ya29.")
    assert body["expires_in"] > 0


async def test_no_token_is_401(cal_client):
    r = await cal_client.get("/calendar/v3/calendars/primary/events")
    assert r.status_code == 401
    assert r.json()["error"]["status"] == "UNAUTHENTICATED"


# ---- calendarList ---------------------------------------------------------

async def test_calendar_list(cal_client, cal_auth):
    r = await cal_client.get("/calendar/v3/users/me/calendarList", headers=cal_auth)
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "calendar#calendarList"
    assert len(body["items"]) == 1
    entry = body["items"][0]
    assert entry["id"] == ALICE and entry["primary"] is True and entry["accessRole"] == "owner"


# ---- events.list: full sync ----------------------------------------------

async def test_full_sync_shape_and_synctoken(cal_client, cal_auth):
    r = await cal_client.get(
        "/calendar/v3/calendars/primary/events"
        "?singleEvents=true&orderBy=startTime&timeMin=2000-01-01T00:00:00Z", headers=cal_auth)
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "calendar#events"
    # default showDeleted=false -> 3 confirmed (the cancelled one is hidden)
    assert len(body["items"]) == 3
    assert "nextSyncToken" in body and "nextPageToken" not in body
    ev = body["items"][0]
    assert ev["kind"] == "calendar#event" and ev["status"] == "confirmed"
    assert "dateTime" in ev["start"] and ev["start"]["timeZone"] == "UTC"
    assert ev["organizer"]["email"] == ALICE
    assert any(a["email"] == BOB for a in ev["attendees"])


async def test_full_sync_pagination(cal_client, cal_auth):
    r = await cal_client.get(
        "/calendar/v3/calendars/primary/events?timeMin=2000-01-01T00:00:00Z&maxResults=2",
        headers=cal_auth)
    body = r.json()
    assert len(body["items"]) == 2
    assert "nextPageToken" in body and "nextSyncToken" not in body
    r2 = await cal_client.get(
        "/calendar/v3/calendars/primary/events?timeMin=2000-01-01T00:00:00Z&maxResults=2"
        f"&pageToken={urllib.parse.quote(body['nextPageToken'])}", headers=cal_auth)
    b2 = r2.json()
    assert len(b2["items"]) == 1 and "nextSyncToken" in b2


# ---- events.list: incremental + showDeleted -------------------------------

async def test_incremental_fresh_token_is_empty(cal_client, cal_auth):
    full = (await cal_client.get(
        "/calendar/v3/calendars/primary/events?timeMin=2000-01-01T00:00:00Z",
        headers=cal_auth)).json()
    sync = full["nextSyncToken"]
    r = await cal_client.get(
        f"/calendar/v3/calendars/primary/events?syncToken={urllib.parse.quote(sync)}", headers=cal_auth)
    body = r.json()
    assert body["items"] == [] and "nextSyncToken" in body


async def test_incremental_from_old_token_returns_changes(cal_client, cal_auth):
    from datetime import datetime, timezone
    old = encode_sync_token(datetime(2020, 1, 1, tzinfo=timezone.utc))
    r = await cal_client.get(
        f"/calendar/v3/calendars/primary/events?syncToken={urllib.parse.quote(old)}&showDeleted=true",
        headers=cal_auth)
    body = r.json()
    assert len(body["items"]) == 4  # showDeleted includes the cancelled event
    cancelled = [e for e in body["items"] if e["status"] == "cancelled"]
    assert len(cancelled) == 1
    # cancelled events are minimal (no start/summary)
    assert "start" not in cancelled[0] and set(cancelled[0]) <= {"kind", "etag", "id", "status", "recurringEventId"}


async def test_synctoken_with_full_sync_param_is_400(cal_client, cal_auth):
    # Real Calendar 400s when syncToken is combined with timeMin/orderBy/etc.
    # (the incremental query-param set is restricted). The mock previously ignored
    # the conflicting params and 200'd — masking a real consumer bug.
    full = (await cal_client.get(
        "/calendar/v3/calendars/primary/events?timeMin=2000-01-01T00:00:00Z",
        headers=cal_auth)).json()
    sync = full["nextSyncToken"]
    for extra in ("timeMin=2000-01-01T00:00:00Z", "orderBy=startTime", "updatedMin=2000-01-01T00:00:00Z"):
        r = await cal_client.get(
            f"/calendar/v3/calendars/primary/events?syncToken={urllib.parse.quote(sync)}&{extra}",
            headers=cal_auth)
        assert r.status_code == 400, extra
        assert r.json()["error"]["code"] == 400


async def test_expired_synctoken_is_410(cal_client, cal_auth):
    for tok in ("EXPIRED", "not-a-real-token"):
        r = await cal_client.get(
            f"/calendar/v3/calendars/primary/events?syncToken={tok}", headers=cal_auth)
        assert r.status_code == 410
        assert r.json()["error"]["errors"][0]["reason"] == "fullSyncRequired"


# ---- reconcile probe ------------------------------------------------------

async def test_updated_min_probe(cal_client, cal_auth):
    r = await cal_client.get(
        "/calendar/v3/calendars/primary/events?updatedMin=2000-01-01T00:00:00Z&maxResults=1",
        headers=cal_auth)
    assert r.status_code == 200
    assert len(r.json()["items"]) == 1


# ---- explicit calendarId == email ----------------------------------------

async def test_calendar_by_email(cal_client, cal_auth):
    r = await cal_client.get(
        f"/calendar/v3/calendars/{urllib.parse.quote(ALICE)}/events?timeMin=2000-01-01T00:00:00Z",
        headers=cal_auth)
    assert r.status_code == 200 and len(r.json()["items"]) == 3


async def test_unknown_calendar_404(cal_client):
    tok = cal_token("nobody@cal-test.com")
    r = await cal_client.get("/calendar/v3/calendars/primary/events",
                             headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 404


# ---- rate limiting --------------------------------------------------------

async def test_rate_limit_429(cal_client, cal_auth):
    # bucket cap 100, cost 1 — 101 fast calls trips it
    saw = None
    for _ in range(130):
        r = await cal_client.get("/calendar/v3/calendars/primary/events?timeMin=2000-01-01T00:00:00Z",
                                 headers=cal_auth)
        if r.status_code == 429:
            saw = r
            break
    assert saw is not None, "expected a 429 under burst"
    assert saw.json()["error"]["errors"][0]["reason"] == "rateLimitExceeded"
    assert int(saw.headers["Retry-After"]) >= 1
