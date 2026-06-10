"""Grafana mock fidelity suite — hard-fail assertions encoding REAL Grafana wire
behavior (annotations HTTP API + Alerting webhook HMAC). A red test here is a
fidelity gap, not a flaky mock.

Audited vs official Grafana docs/source (grafana.com/docs + ItemDTO):
  * GET /api/annotations returns a BARE JSON ARRAY, newest-first, epoch-ms window,
    no cursor/Link — pagination is a backward time-window walk (short page = EOF).
  * ItemDTO is omitempty on every field: a plain annotation omits
    alertId/newState/prevState; an alert-state-change annotation omits
    userId/login/email and carries alertId/alertName/newState/prevState.
  * Auth is a service-account Bearer; missing/blank -> 401 {"message":...}.
  * Alerting webhook (12.0+) signs X-Grafana-Alerting-Signature = bare lowercase
    hex HMAC-SHA256 over the raw body (NO sha256= prefix).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


# --------------------------------------------------------------------- auth

async def test_missing_auth_is_401_message_envelope(grafana_client):
    r = await grafana_client.get("/api/annotations")
    assert r.status_code == 401
    body = r.json()
    # Grafana's error envelope: `message` is the one guaranteed field.
    assert "message" in body and body["message"]
    assert "ok" not in body  # not a slack-shaped error


async def test_org_probe(grafana_client, grafana_auth):
    r = await grafana_client.get("/api/org", headers=grafana_auth)
    assert r.status_code == 200
    body = r.json()
    # current-org endpoint returns id + name only (no address sub-object)
    assert body == {"id": 1, "name": "Alpen Labs"}


# ------------------------------------------------------------- annotations shape

async def test_annotations_is_bare_array(grafana_client, grafana_auth):
    r = await grafana_client.get("/api/annotations", headers=grafana_auth)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list), "annotations endpoint must return a BARE JSON array"
    assert len(body) == 5


async def test_epoch_ms_integer_timestamps(grafana_client, grafana_auth):
    body = (await grafana_client.get("/api/annotations", headers=grafana_auth)).json()
    a = body[0]
    for k in ("time", "timeEnd", "created", "updated"):
        assert isinstance(a[k], int), f"{k} must be an epoch-ms integer, got {type(a[k])}"
    # value is plausibly epoch-MILLIS (13 digits), not seconds
    assert a["time"] > 1_000_000_000_000


async def test_newest_first_ordering(grafana_client, grafana_auth):
    body = (await grafana_client.get("/api/annotations", headers=grafana_auth)).json()
    times = [a["time"] for a in body]
    assert times == sorted(times, reverse=True), "annotations must be newest-first"


async def test_alert_annotation_omitempty_shape(grafana_client, grafana_auth):
    """An alert-state-change annotation carries alertId/newState/prevState and
    OMITS the user fields (machine-authored, userId 0)."""
    body = (await grafana_client.get("/api/annotations?type=alert", headers=grafana_auth)).json()
    assert body, "expected alert annotations"
    for a in body:
        assert a["alertId"] > 0
        assert a["newState"] in ("Alerting", "Normal")
        assert a["prevState"] in ("Alerting", "Normal")
        assert "alertName" in a
        # omitempty: machine annotation drops the user identity entirely
        assert "userId" not in a
        assert "login" not in a
        assert "email" not in a


async def test_plain_annotation_omits_alert_fields(grafana_client, grafana_auth):
    """A user/deploy annotation has user identity and OMITS the alert fields —
    a modern (omitempty) instance never emits alertId:0 / newState:''."""
    body = (await grafana_client.get("/api/annotations?type=annotation", headers=grafana_auth)).json()
    assert body, "expected plain annotations"
    for a in body:
        assert "alertId" not in a, "omitempty: a plain annotation must drop alertId, not send 0"
        assert "newState" not in a
        assert "prevState" not in a
        assert a["userId"] > 0
        assert a["login"]


async def test_type_filter_splits_streams(grafana_client, grafana_auth):
    everything = (await grafana_client.get("/api/annotations", headers=grafana_auth)).json()
    alerts = (await grafana_client.get("/api/annotations?type=alert", headers=grafana_auth)).json()
    plain = (await grafana_client.get("/api/annotations?type=annotation", headers=grafana_auth)).json()
    assert len(alerts) + len(plain) == len(everything)
    assert all("alertId" in a for a in alerts)
    assert all("alertId" not in a for a in plain)


async def test_tags_and_filter(grafana_client, grafana_auth):
    body = (await grafana_client.get("/api/annotations?tags=deploy", headers=grafana_auth)).json()
    assert body
    assert all("deploy" in a.get("tags", []) for a in body)
    # a tag nothing carries -> empty
    none = (await grafana_client.get("/api/annotations?tags=nonexistent", headers=grafana_auth)).json()
    assert none == []


# ------------------------------------------------------- backward time-window walk

async def test_window_from_to_filters_by_time(grafana_client, grafana_auth):
    full = (await grafana_client.get("/api/annotations", headers=grafana_auth)).json()
    newest, oldest = full[0]["time"], full[-1]["time"]
    # upper bound excludes the newest annotation (backward-walk cursor = min-1ms)
    page = (await grafana_client.get(
        f"/api/annotations?to={newest - 1}", headers=grafana_auth)).json()
    assert all(a["time"] <= newest - 1 for a in page)
    assert newest not in [a["time"] for a in page]
    # lower bound
    page2 = (await grafana_client.get(
        f"/api/annotations?from={oldest + 1}", headers=grafana_auth)).json()
    assert all(a["time"] >= oldest + 1 for a in page2)


async def test_limit_and_short_page_is_eof(grafana_client, grafana_auth):
    page = (await grafana_client.get("/api/annotations?limit=2", headers=grafana_auth)).json()
    assert len(page) == 2
    # walk: next page upper bound = min(time)-1
    to = min(a["time"] for a in page) - 1
    page2 = (await grafana_client.get(
        f"/api/annotations?limit=2&to={to}", headers=grafana_auth)).json()
    assert len(page2) == 2
    assert set(a["id"] for a in page) & set(a["id"] for a in page2) == set()
    # remaining annotation is a short (<limit) page -> EOF
    to3 = min(a["time"] for a in page2) - 1
    page3 = (await grafana_client.get(
        f"/api/annotations?limit=2&to={to3}", headers=grafana_auth)).json()
    assert len(page3) < 2


# ------------------------------------------------------------ rate-limit control

async def test_forced_429_envelope(grafana_client, grafana_auth):
    from spammers.grafana.app import _FORCED_429
    await grafana_client.post("/_control/rate_limit?count=1")
    r = await grafana_client.get("/api/annotations", headers=grafana_auth)
    assert r.status_code == 429
    assert "message" in r.json()
    _FORCED_429["count"] = 0
    # recovers on the next call
    r2 = await grafana_client.get("/api/annotations", headers=grafana_auth)
    assert r2.status_code == 200
