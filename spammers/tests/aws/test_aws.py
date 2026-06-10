"""AWS mock fidelity — hard-fail assertions on the CloudTrail (JSON 1.1) +
STS (Query) wire contracts and SigV4 verification.

Every check encodes a REAL AWS behavior; a divergence fails the suite (the audit
posture). The mock is exercised exactly as botocore drives it: a single ``POST /``
dispatched on ``X-Amz-Target`` (CloudTrail) or the form ``Action`` (STS), every
request SigV4-signed.
"""
from __future__ import annotations

import json
from xml.etree import ElementTree as ET

import pytest

from .conftest import (CT_TARGET, EVENTS, cloudtrail_headers, sts_headers)

pytestmark = pytest.mark.asyncio(loop_scope="session")

_NS = "{https://sts.amazonaws.com/doc/2011-06-15/}"


async def _lookup(client, body: dict, **sign_kw):
    raw = json.dumps(body).encode()
    return await client.post("/", content=raw, headers=cloudtrail_headers(raw, **sign_kw))


# --------------------------------------------------------------------------- CloudTrail


async def test_lookup_events_envelope(aws_client):
    r = await _lookup(aws_client, {"MaxResults": 50})
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["Events"], list) and len(body["Events"]) == len(EVENTS)
    ev = body["Events"][0]
    # Event wrapper field set.
    assert {"EventId", "EventName", "ReadOnly", "EventTime", "EventSource",
            "Resources", "CloudTrailEvent"} <= set(ev)
    # ReadOnly is the STRING "true"/"false" (JSON-1.1 contract), NOT a bool.
    assert isinstance(ev["ReadOnly"], str) and ev["ReadOnly"] in ("true", "false")
    # EventTime is epoch SECONDS as a NUMBER (the JSON-1.1 timestamp encoding).
    assert isinstance(ev["EventTime"], (int, float)) and not isinstance(ev["EventTime"], bool)
    assert ev["EventTime"] < 1_000_000_000_000  # seconds, not millis
    # CloudTrailEvent is a JSON-encoded STRING, not a nested object.
    assert isinstance(ev["CloudTrailEvent"], str)
    assert isinstance(json.loads(ev["CloudTrailEvent"]), dict)
    assert isinstance(ev["Resources"], list)


async def test_cloudtrail_event_blob(aws_client):
    r = await _lookup(aws_client, {"MaxResults": 50})
    for ev in r.json()["Events"]:
        rec = json.loads(ev["CloudTrailEvent"])
        assert str(rec["eventID"]) == str(ev["EventId"])          # immutable dedup key
        assert rec["eventVersion"].startswith("1.")
        # native eventTime is RFC3339 `…Z`, NOT epoch (distinct from the wrapper).
        assert rec["eventTime"].endswith("Z") and "T" in rec["eventTime"]
        assert rec["eventCategory"] == "Management"
        assert rec["recipientAccountId"] == "905418473921"
        assert rec["awsRegion"] == "us-east-1"
        assert isinstance(rec["userIdentity"], dict)


async def test_newest_first_and_full_set(aws_client):
    r = await _lookup(aws_client, {"MaxResults": 50})
    times = [ev["EventTime"] for ev in r.json()["Events"]]
    assert times == sorted(times, reverse=True)  # most-recent-first
    ids = {ev["EventId"] for ev in r.json()["Events"]}
    assert ids == {e[0] for e in EVENTS}


async def test_pagination_nexttoken(aws_client):
    seen, token, pages = [], None, 0
    while True:
        body = {"MaxResults": 2}
        if token:
            body["NextToken"] = token
        r = await _lookup(aws_client, body)
        assert r.status_code == 200, r.text
        page = r.json()
        assert len(page["Events"]) <= 2  # honored MaxResults
        seen.extend(e["EventId"] for e in page["Events"])
        token = page.get("NextToken")
        pages += 1
        if not token:
            break
        assert pages < 20
    assert len(seen) == len(EVENTS) == len(set(seen))  # all events, no dupes
    assert pages == 3  # 5 events / 2 per page → 3 pages, last has no token


async def test_max_results_cap(aws_client):
    r = await _lookup(aws_client, {"MaxResults": 51})
    assert r.status_code == 400
    assert r.json()["__type"] == "InvalidMaxResultsException"


async def test_invalid_next_token(aws_client):
    # A token minted for a different window must be rejected.
    r1 = await _lookup(aws_client, {"MaxResults": 2})
    token = r1.json()["NextToken"]
    r2 = await _lookup(aws_client, {"MaxResults": 2, "NextToken": token,
                                    "StartTime": 1, "EndTime": 2})
    assert r2.status_code == 400
    assert r2.json()["__type"] == "InvalidNextTokenException"


async def test_window_filter(aws_client):
    # A window covering only the last two seeded events (hours 3 and 4).
    base_s = EVENTS[0][1] // 1000
    r = await _lookup(aws_client, {"StartTime": base_s + 3 * 3600, "EndTime": base_s + 9 * 3600})
    ids = {e["EventId"] for e in r.json()["Events"]}
    assert ids == {EVENTS[3][0], EVENTS[4][0]}


async def test_lookup_attribute_filter(aws_client):
    r = await _lookup(aws_client, {"MaxResults": 50, "LookupAttributes": [
        {"AttributeKey": "EventName", "AttributeValue": "CreateUser"}]})
    ids = [e["EventId"] for e in r.json()["Events"]]
    assert ids == [EVENTS[4][0]]


async def test_multiple_lookup_attributes_rejected(aws_client):
    r = await _lookup(aws_client, {"LookupAttributes": [
        {"AttributeKey": "EventName", "AttributeValue": "CreateUser"},
        {"AttributeKey": "EventSource", "AttributeValue": "iam.amazonaws.com"}]})
    assert r.status_code == 400
    assert r.json()["__type"] == "InvalidLookupAttributesException"


async def test_alarm_vs_management_split(aws_client):
    r = await _lookup(aws_client, {"MaxResults": 50})
    alarms, mgmt = [], []
    for ev in r.json()["Events"]:
        rec = json.loads(ev["CloudTrailEvent"])
        (alarms if rec.get("alarmName") else mgmt).append(rec)
    assert len(alarms) == 2 and len(mgmt) == 3
    for a in alarms:
        assert a["newState"] in ("OK", "ALARM", "INSUFFICIENT_DATA")
        assert a["eventSource"] == "monitoring.amazonaws.com"
        assert a["userIdentity"]["type"] == "AWSService"  # actorless / machine
    for m in mgmt:
        assert "alarmName" not in m
        assert m["userIdentity"].get("arn", "").startswith("arn:aws:iam::")


# --------------------------------------------------------------------------- SigV4


async def test_sigv4_valid_accepted(aws_client):
    r = await _lookup(aws_client, {"MaxResults": 1})
    assert r.status_code == 200


async def test_sigv4_tamper_rejected(aws_client):
    # Sign with the WRONG secret → the mock recomputes with the real secret → 403.
    raw = json.dumps({"MaxResults": 1}).encode()
    headers = cloudtrail_headers(raw, secret="this-is-not-the-right-secret-at-all")
    r = await aws_client.post("/", content=raw, headers=headers)
    assert r.status_code == 403
    assert r.json()["__type"] == "SignatureDoesNotMatch"
    assert r.headers.get("x-amzn-ErrorType") == "SignatureDoesNotMatch"


async def test_sigv4_unknown_access_key(aws_client):
    raw = json.dumps({"MaxResults": 1}).encode()
    headers = cloudtrail_headers(raw, access_key="AKIAUNKNOWNKEY999999")
    r = await aws_client.post("/", content=raw, headers=headers)
    assert r.status_code == 403
    assert r.json()["__type"] == "InvalidClientTokenId"


async def test_missing_authorization(aws_client):
    raw = json.dumps({"MaxResults": 1}).encode()
    r = await aws_client.post("/", content=raw,
                              headers={"X-Amz-Target": CT_TARGET,
                                       "Content-Type": "application/x-amz-json-1.1"})
    assert r.status_code == 403
    assert r.json()["__type"] == "MissingAuthenticationToken"


async def test_forced_throttle(aws_client):
    await aws_client.post("/_control/rate_limit", params={"count": 1})
    r = await _lookup(aws_client, {"MaxResults": 1})
    assert r.status_code == 400
    assert r.json()["__type"] == "ThrottlingException"
    # next call recovers
    r2 = await _lookup(aws_client, {"MaxResults": 1})
    assert r2.status_code == 200


# --------------------------------------------------------------------------- STS


async def test_sts_get_caller_identity(aws_client):
    raw = b"Action=GetCallerIdentity&Version=2011-06-15"
    r = await aws_client.post("/", content=raw, headers=sts_headers(raw))
    assert r.status_code == 200, r.text
    root = ET.fromstring(r.text)
    result = root.find(f"{_NS}GetCallerIdentityResult")
    assert result.find(f"{_NS}Account").text == "905418473921"
    assert result.find(f"{_NS}Arn").text.endswith(":user/fyralis-ingest")
    assert result.find(f"{_NS}UserId").text == "AIDATESTFIDELITY0001"


async def test_sts_assume_role_and_temp_cred_call(aws_client):
    raw = (b"Action=AssumeRole&Version=2011-06-15&RoleArn="
           b"arn%3Aaws%3Aiam%3A%3A905418473921%3Arole%2FFyralisIngestReadOnly"
           b"&RoleSessionName=probe")
    r = await aws_client.post("/", content=raw, headers=sts_headers(raw))
    assert r.status_code == 200, r.text
    root = ET.fromstring(r.text)
    creds = root.find(f"{_NS}AssumeRoleResult/{_NS}Credentials")
    ak = creds.find(f"{_NS}AccessKeyId").text
    sk = creds.find(f"{_NS}SecretAccessKey").text
    tok = creds.find(f"{_NS}SessionToken").text
    assert ak.startswith("ASIA")
    assert creds.find(f"{_NS}Expiration").text.endswith("Z")
    aru = root.find(f"{_NS}AssumeRoleResult/{_NS}AssumedRoleUser")
    assert aru.find(f"{_NS}Arn").text.startswith("arn:aws:sts::905418473921:assumed-role/")
    # Use the temp creds (session token) to call CloudTrail — the AssumeRole path.
    raw2 = json.dumps({"MaxResults": 1}).encode()
    headers = cloudtrail_headers(raw2, access_key=ak, secret=sk, security_token=tok)
    r2 = await aws_client.post("/", content=raw2, headers=headers)
    assert r2.status_code == 200, r2.text
    assert isinstance(r2.json()["Events"], list)


async def test_sts_bad_signature(aws_client):
    raw = b"Action=GetCallerIdentity&Version=2011-06-15"
    headers = sts_headers(raw, secret="wrong-secret")
    r = await aws_client.post("/", content=raw, headers=headers)
    assert r.status_code == 403
    root = ET.fromstring(r.text)
    assert root.find(f"{_NS}Error/{_NS}Code").text == "SignatureDoesNotMatch"


# --------------------------------------------------------------------------- health


async def test_health_no_auth(aws_client):
    r = await aws_client.get("/_health")
    assert r.status_code == 200
    assert r.json()["account_id"] == "905418473921"
