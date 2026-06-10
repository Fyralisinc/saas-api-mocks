"""Fixtures for the AWS mock fidelity suite (CloudTrail LookupEvents + STS, SigV4).

Seeds a deterministic install (account/region + a static IAM key pair) plus a
small CloudTrail event stream: management events + an alarm-state-change pair,
with staggered epoch-ms timestamps. Wires the AWS ``state`` singleton + an ASGI
client, and provides a self-contained SigV4 signer (botocore isn't in the spammer
venv — the real-botocore wire compatibility is proven separately by the
ingest-client slice; here we sign independently to drive the endpoints and to
exercise the verifier's accept/reject behavior).
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

ACCOUNT_ID = "905418473921"
REGION = "us-east-1"
ACCESS_KEY_ID = "AKIATESTFIDELITY0001"
SECRET_ACCESS_KEY = "testFidelitySecretAccessKey0123456789abcdef"
ROLE_ARN = f"arn:aws:iam::{ACCOUNT_ID}:role/FyralisIngestReadOnly"
IAM_USER_ARN = f"arn:aws:iam::{ACCOUNT_ID}:user/fyralis-ingest"
USER_ID = "AIDATESTFIDELITY0001"
CT_TARGET = "com.amazonaws.cloudtrail.v20131101.CloudTrail_20131101.LookupEvents"
HOST = "mock"

# 2026-10-01T00:00:00Z in epoch ms
_T0_MS = 1759276800000
_HOUR = 3_600_000


def _mgmt_record(eid, name, source, when_ms, read_only, handle):
    iso = datetime.fromtimestamp(when_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "eventVersion": "1.11",
        "userIdentity": {"type": "IAMUser", "principalId": "AIDA" + handle.upper(),
                         "arn": f"arn:aws:iam::{ACCOUNT_ID}:user/{handle}",
                         "accountId": ACCOUNT_ID, "accessKeyId": "AKIA" + handle.upper(),
                         "userName": handle},
        "eventTime": iso, "eventSource": source, "eventName": name,
        "awsRegion": REGION, "sourceIPAddress": "203.0.113.10",
        "userAgent": "aws-cli/2.15.17",
        "requestParameters": {"resourceName": "i-abc123"},
        "responseElements": None if read_only else {"requestId": eid},
        "requestID": eid, "eventID": eid, "readOnly": read_only,
        "eventType": "AwsApiCall", "managementEvent": True,
        "recipientAccountId": ACCOUNT_ID, "eventCategory": "Management",
    }


def _alarm_record(eid, name, new_state, prev_state, when_ms):
    iso = datetime.fromtimestamp(when_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "eventVersion": "1.08",
        "userIdentity": {"accountId": ACCOUNT_ID, "type": "AWSService",
                         "invokedBy": "monitoring.amazonaws.com"},
        "eventTime": iso, "eventSource": "monitoring.amazonaws.com",
        "eventName": "SetAlarmState", "awsRegion": REGION,
        "sourceIPAddress": "monitoring.amazonaws.com",
        "userAgent": "monitoring.amazonaws.com",
        "requestParameters": {"alarmName": name, "stateValue": new_state},
        "responseElements": None, "requestID": eid, "eventID": eid,
        "readOnly": False, "eventType": "AwsServiceEvent", "managementEvent": True,
        "recipientAccountId": ACCOUNT_ID, "eventCategory": "Management",
        "alarmName": name, "newState": new_state, "prevState": prev_state,
    }


# (event_id, ms, name, source, read_only, is_alarm, alarm_args)
EVENTS = [
    ("11111111-1111-1111-1111-111111111111", _T0_MS + 0 * _HOUR,
     "RunInstances", "ec2.amazonaws.com", False, False, None),
    ("22222222-2222-2222-2222-222222222222", _T0_MS + 1 * _HOUR,
     "DescribeInstances", "ec2.amazonaws.com", True, False, None),
    ("33333333-3333-3333-3333-333333333333", _T0_MS + 2 * _HOUR,
     "SetAlarmState", "monitoring.amazonaws.com", False, True, ("api-5xx-high", "ALARM", "OK")),
    ("44444444-4444-4444-4444-444444444444", _T0_MS + 3 * _HOUR,
     "SetAlarmState", "monitoring.amazonaws.com", False, True, ("api-5xx-high", "OK", "ALARM")),
    ("55555555-5555-5555-5555-555555555555", _T0_MS + 4 * _HOUR,
     "CreateUser", "iam.amazonaws.com", False, False, None),
]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def aws_run(pool) -> UUID:
    run_id = uuid4()
    await pool.execute(
        """INSERT INTO org.runs (id, size, runtime, seed, fyralis_tenant_id,
               fyralis_base_url, virtual_now, mode, speed_multiplier)
           VALUES ($1,'small','few_months',7,$2,'http://localhost:8000',now(),'frozen',1.0)""",
        run_id, uuid4())
    inst_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_aws.installations
            (id, run_id, account_id, region, endpoint_host, access_key_id,
             secret_access_key, role_arn, external_id, iam_user_arn, user_id, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,now())""",
        inst_pk, run_id, ACCOUNT_ID, REGION, f"cloudtrail.{REGION}.amazonaws.com",
        ACCESS_KEY_ID, SECRET_ACCESS_KEY, ROLE_ARN, "ext-id-1", IAM_USER_ARN, USER_ID)
    for (eid, ms, name, source, ro, is_alarm, alarm) in EVENTS:
        if is_alarm:
            rec = _alarm_record(eid, alarm[0], alarm[1], alarm[2], ms)
            username, akid, resources = "", "", []
        else:
            rec = _mgmt_record(eid, name, source, ms, ro, "alice")
            username, akid = "alice", "AKIAALICE"
            resources = [{"ResourceType": "AWS::EC2::Instance", "ResourceName": "i-abc123"}]
        await pool.execute(
            """INSERT INTO app_aws.events
                (id, install_pk, event_id, event_time_ms, event_name, event_source,
                 aws_region, username, access_key_id, read_only, resources, record,
                 is_alarm, created_at, is_historical)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12::jsonb,$13,$14,TRUE)""",
            uuid4(), inst_pk, eid, ms, name, source, REGION, username, akid, ro,
            json.dumps(resources), json.dumps(rec), is_alarm,
            datetime.fromtimestamp(ms / 1000, tz=timezone.utc))
    return run_id


@pytest_asyncio.fixture(loop_scope="session")
async def aws_client(pool, aws_run):
    from spammers.aws import state as a_state
    from spammers.aws.app import create_app, _FORCED_THROTTLE
    from spammers.aws import sigv4 as a_sigv4

    a_state._STATE = a_state.AwsMockState(pool=pool, run_id=aws_run)
    _FORCED_THROTTLE["count"] = 0
    a_sigv4._TEMP_CREDS.clear()
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url=f"http://{HOST}") as c:
        yield c
    a_state._STATE = None


# --------------------------------------------------------------------------- SigV4 signer


def _sigv4_headers(*, body: bytes, content_type: str, service: str,
                   access_key: str = ACCESS_KEY_ID, secret: str = SECRET_ACCESS_KEY,
                   region: str = REGION, target: str | None = None,
                   security_token: str | None = None,
                   amzdate: str = "20261001T000000Z") -> dict[str, str]:
    """Produce a valid SigV4 Authorization header (independent of the mock's
    verifier) for a POST / request. ``host`` is the ASGI base-url host (``mock``).
    """
    datestamp = amzdate[:8]
    headers = {"host": HOST, "x-amz-date": amzdate, "content-type": content_type}
    if target:
        headers["x-amz-target"] = target
    if security_token:
        headers["x-amz-security-token"] = security_token
    signed = sorted(headers)
    canonical_headers = "".join(f"{k}:{' '.join(headers[k].split())}\n" for k in signed)
    signed_headers = ";".join(signed)
    payload_hash = hashlib.sha256(body).hexdigest()
    canonical_request = "\n".join(["POST", "/", "", canonical_headers, signed_headers, payload_hash])
    scope = f"{datestamp}/{region}/{service}/aws4_request"
    sts = "\n".join(["AWS4-HMAC-SHA256", amzdate, scope,
                     hashlib.sha256(canonical_request.encode()).hexdigest()])

    def _s(key, msg):
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    k_date = _s(("AWS4" + secret).encode(), datestamp)
    k_signing = _s(_s(_s(k_date, region), service), "aws4_request")
    sig = hmac.new(k_signing, sts.encode(), hashlib.sha256).hexdigest()
    auth = (f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={sig}")
    # httpx sets the Host header itself; return the rest (incl. Authorization).
    out = {"X-Amz-Date": amzdate, "Content-Type": content_type, "Authorization": auth}
    if target:
        out["X-Amz-Target"] = target
    if security_token:
        out["X-Amz-Security-Token"] = security_token
    return out


def cloudtrail_headers(body: bytes, **kw) -> dict[str, str]:
    return _sigv4_headers(body=body, content_type="application/x-amz-json-1.1",
                          service="cloudtrail", target=CT_TARGET, **kw)


def sts_headers(body: bytes, **kw) -> dict[str, str]:
    return _sigv4_headers(body=body,
                          content_type="application/x-www-form-urlencoded; charset=utf-8",
                          service="sts", **kw)
