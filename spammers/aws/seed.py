"""Realistic AWS CloudTrail corpus seeding.

AWS is a NET-NEW Tier-C source: the frozen run has no AWS corpus, so we model a
realistic control-plane stream ourselves (the brief sanctions this, like Grafana
projecting an annotation timeline). We synthesize the one ``app_aws.events``
stream a real Organization CloudTrail would accumulate over the last ~88 days
(CloudTrail's ``LookupEvents`` 90-day retention) — and that stream, faithfully,
carries BOTH:

  * **management events** — every control-plane API call: who (``userIdentity``)
    did what (``eventName``/``eventSource``) to which resource, when. The bulk of
    the stream. ``is_alarm`` FALSE, no ``alarmName`` in the record → ``signal``.
  * **CloudWatch alarm-state changes** — OK↔ALARM transitions (firing→resolved
    pairs), machine-authored (``userIdentity`` type ``AWSService``, actorless),
    carrying top-level ``alarmName``/``newState``/``prevState`` → ``state_change``.

Everything is **deterministic** off the run seed and derived from the org already
in the run (people → IAM principals, GitHub repos → service names). Each event row
stores the full native CloudTrail record in ``record`` (→ the ``CloudTrailEvent``
JSON string) plus the projection columns the LookupEvents ``Event`` wrapper needs.
Idempotent: a second call after the install row exists is a no-op.
"""
from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

import asyncpg

# Seed-stable install identity (hand these to the ingest-client / memory).
ACCOUNT_ID = "905418473921"
REGION = "us-east-1"
ENDPOINT_HOST = f"cloudtrail.{REGION}.amazonaws.com"
ACCESS_KEY_ID = "AKIAALPENLABSMOCK01"
SECRET_ACCESS_KEY = "Alpenlabs0MockSecretAccessKeyFyralisIngest9f1d3b7c"
ROLE_ARN = f"arn:aws:iam::{ACCOUNT_ID}:role/FyralisIngestReadOnly"
EXTERNAL_ID = "fyralis-alpenlabs-7f3a9c2e"
IAM_USER_ARN = f"arn:aws:iam::{ACCOUNT_ID}:user/fyralis-ingest"
USER_ID = "AIDAALPENLABSINGEST01"

_BACKFILL_DAYS = 88  # within CloudTrail's 90-day LookupEvents retention

_FALLBACK_SERVICES = [
    "strata-node", "strata-bridge", "strata-p2p", "zkaleido", "faucet-api",
    "checkpoint-explorer", "alpen-reth", "prover-worker",
]
_USER_AGENTS = [
    "aws-cli/2.15.17 Python/3.11.8 Linux/6.5.0 exec-env/CloudShell",
    "Boto3/1.34.69 md/Botocore#1.34.69 ua/2.0 os/linux#6.5 lang/python#3.11.8",
    "terraform/1.7.5 (+https://www.terraform.io) terraform-provider-aws/5.42.0",
    "console.amazonaws.com",
    "eks.amazonaws.com",
    "cloudformation.amazonaws.com",
]

# (eventName, eventSource, readOnly, resource_type, mk_resource_name, mutating)
# mutating=True events carry a responseElements; readOnly=True omit it.
_MGMT = [
    ("RunInstances", "ec2.amazonaws.com", False, "AWS::EC2::Instance", "i-"),
    ("TerminateInstances", "ec2.amazonaws.com", False, "AWS::EC2::Instance", "i-"),
    ("AuthorizeSecurityGroupIngress", "ec2.amazonaws.com", False, "AWS::EC2::SecurityGroup", "sg-"),
    ("CreateImage", "ec2.amazonaws.com", False, "AWS::EC2::Image", "ami-"),
    ("CreateBucket", "s3.amazonaws.com", False, "AWS::S3::Bucket", "bucket-"),
    ("PutBucketPolicy", "s3.amazonaws.com", False, "AWS::S3::Bucket", "bucket-"),
    ("DeleteBucket", "s3.amazonaws.com", False, "AWS::S3::Bucket", "bucket-"),
    ("CreateFunction20150331", "lambda.amazonaws.com", False, "AWS::Lambda::Function", "fn-"),
    ("UpdateFunctionCode20150331v2", "lambda.amazonaws.com", False, "AWS::Lambda::Function", "fn-"),
    ("CreateUser", "iam.amazonaws.com", False, "AWS::IAM::User", "user-"),
    ("AttachUserPolicy", "iam.amazonaws.com", False, "AWS::IAM::User", "user-"),
    ("CreateAccessKey", "iam.amazonaws.com", False, "AWS::IAM::AccessKey", "AKIA"),
    ("CreateRole", "iam.amazonaws.com", False, "AWS::IAM::Role", "role-"),
    ("CreateKey", "kms.amazonaws.com", False, "AWS::KMS::Key", "key-"),
    ("CreateStack", "cloudformation.amazonaws.com", False, "AWS::CloudFormation::Stack", "stack-"),
    ("CreateDBInstance", "rds.amazonaws.com", False, "AWS::RDS::DBInstance", "db-"),
    ("PutRule", "events.amazonaws.com", False, "AWS::Events::Rule", "rule-"),
    ("CreateLogGroup", "logs.amazonaws.com", False, "AWS::Logs::LogGroup", "/aws/lambda/"),
    # read-only calls (signal, responseElements null)
    ("DescribeInstances", "ec2.amazonaws.com", True, "AWS::EC2::Instance", "i-"),
    ("GetObject", "s3.amazonaws.com", True, "AWS::S3::Object", "obj-"),
    ("ListBuckets", "s3.amazonaws.com", True, None, ""),
    ("Decrypt", "kms.amazonaws.com", True, "AWS::KMS::Key", "key-"),
    ("AssumeRole", "sts.amazonaws.com", False, "AWS::IAM::Role", "role-"),
]
# CloudWatch alarms (name, metric, namespace, dimension)
_ALARMS = [
    ("api-gateway-5xx-high", "5XXError", "AWS/ApiGateway", "ApiName"),
    ("prover-worker-cpu-high", "CPUUtilization", "AWS/EC2", "InstanceId"),
    ("rds-free-storage-low", "FreeStorageSpace", "AWS/RDS", "DBInstanceIdentifier"),
    ("bridge-lag-high", "OperatorLag", "Strata/Bridge", "Operator"),
    ("nlb-unhealthy-hosts", "UnHealthyHostCount", "AWS/NetworkELB", "LoadBalancer"),
    ("lambda-error-rate-high", "Errors", "AWS/Lambda", "FunctionName"),
]


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _guid(rng: random.Random) -> str:
    return str(UUID(int=rng.getrandbits(128)))


def _ip(rng: random.Random) -> str:
    return ".".join(str(rng.randint(1, 254)) for _ in range(4))


def _stable_id(prefix: str, handle: str, n: int = 17) -> str:
    h = hashlib.blake2b(handle.encode(), digest_size=12).hexdigest().upper()
    return prefix + h[:n]


async def _service_names(pool: asyncpg.Pool, run_id: UUID) -> list[str]:
    try:
        rows = await pool.fetch(
            "SELECT DISTINCT r.name FROM app_github.repositories r "
            "JOIN app_github.installations i ON i.id = r.installation_pk "
            "WHERE i.run_id = $1 ORDER BY r.name", run_id)
        names = [r["name"] for r in rows if r["name"] and not r["name"].startswith(".")]
    except asyncpg.PostgresError:
        names = []
    return names or list(_FALLBACK_SERVICES)


def _principal(account: str, handle: str) -> dict:
    return {
        "type": "IAMUser",
        "principalId": _stable_id("AIDA", handle),
        "arn": f"arn:aws:iam::{account}:user/{handle}",
        "accountId": account,
        "accessKeyId": _stable_id("AKIA", handle + "key"),
        "userName": handle,
        "sessionContext": {
            "sessionIssuer": {},
            "attributes": {"creationDate": "", "mfaAuthenticated": "false"},
        },
    }


def _mgmt_record(rng, *, account, region, principal, name, source, read_only,
                 res_type, res_prefix, service, when) -> tuple[dict, list]:
    res_name = (res_prefix + service if res_prefix.startswith("/") or "-" in res_prefix
                else res_prefix + hashlib.blake2b(
                    (name + service + str(_ms(when))).encode(), digest_size=6).hexdigest())
    resources = []
    if res_type:
        resources = [{"ResourceType": res_type, "ResourceName": res_name}]
    record = {
        "eventVersion": "1.11",
        "userIdentity": principal,
        "eventTime": _iso(when),
        "eventSource": source,
        "eventName": name,
        "awsRegion": region,
        "sourceIPAddress": _ip(rng),
        "userAgent": rng.choice(_USER_AGENTS),
        "requestParameters": {"resourceName": res_name, "service": service},
        "responseElements": None if read_only else {"requestId": _guid(rng), "_result": "ok"},
        "requestID": _guid(rng),
        "eventID": _guid(rng),
        "readOnly": read_only,
        "eventType": "AwsApiCall",
        "managementEvent": True,
        "recipientAccountId": account,
        "eventCategory": "Management",
        "tlsDetails": {
            "tlsVersion": "TLSv1.3",
            "cipherSuite": "TLS_AES_128_GCM_SHA256",
            "clientProvidedHostHeader": source,
        },
    }
    return record, resources


def _alarm_record(rng, *, account, region, alarm, new_state, prev_state, when,
                  service) -> dict:
    name, metric, namespace, dim = alarm
    reason = (f"Threshold Crossed: 1 datapoint for metric {metric} "
              f"was {'greater' if new_state == 'ALARM' else 'less'} than the threshold.")
    return {
        "eventVersion": "1.08",
        "userIdentity": {"accountId": account, "invokedBy": "monitoring.amazonaws.com",
                         "type": "AWSService"},
        "eventTime": _iso(when),
        "eventSource": "monitoring.amazonaws.com",
        "eventName": "SetAlarmState",
        "awsRegion": region,
        "sourceIPAddress": "monitoring.amazonaws.com",
        "userAgent": "monitoring.amazonaws.com",
        "requestParameters": {"alarmName": name, "stateValue": new_state,
                              "stateReason": reason},
        "responseElements": None,
        "requestID": _guid(rng),
        "eventID": _guid(rng),
        "readOnly": False,
        "eventType": "AwsServiceEvent",
        "managementEvent": True,
        "recipientAccountId": account,
        "eventCategory": "Management",
        # The alarm-transition discriminator the consumer keys on (top-level).
        "alarmName": name,
        "newState": new_state,
        "prevState": prev_state,
        "serviceEventDetails": {
            "metricNamespace": namespace, "metricName": metric,
            "dimensions": {dim: service}, "newState": new_state, "previousState": prev_state,
        },
    }


async def seed_aws(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    at: Optional[datetime] = None,
) -> dict[str, int]:
    """Provision the install + a realistic CloudTrail event stream for ``run_id``.

    Idempotent. Returns ``{"events": N, "alarms": M}`` (zeros if already seeded)."""
    existing = await pool.fetchval(
        "SELECT id FROM app_aws.installations WHERE run_id = $1", run_id)
    if existing is not None:
        return {"events": 0, "alarms": 0}

    seed_row = await pool.fetchrow(
        "SELECT seed, virtual_now FROM org.runs WHERE id = $1", run_id)
    if seed_row is None:
        raise LookupError(f"no run {run_id}")
    rng = random.Random(int(seed_row["seed"]) ^ 0x0061_7773)  # 'aws'
    now = at or seed_row["virtual_now"] or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    inst_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_aws.installations
            (id, run_id, account_id, region, endpoint_host, access_key_id,
             secret_access_key, role_arn, external_id, iam_user_arn, user_id, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)""",
        inst_pk, run_id, ACCOUNT_ID, REGION, ENDPOINT_HOST, ACCESS_KEY_ID,
        SECRET_ACCESS_KEY, ROLE_ARN, EXTERNAL_ID, IAM_USER_ARN, USER_ID, now)

    people = await pool.fetch(
        "SELECT handle FROM org.people WHERE run_id = $1 ORDER BY handle", run_id)
    handles = [p["handle"] for p in people] or ["ops", "deploy-bot", "sre"]
    services = await _service_names(pool, run_id)

    window_start = now - timedelta(days=_BACKFILL_DAYS)
    rows: list[tuple] = []  # (event_id, ms, name, source, region, username, akid, ro, resources, record, is_alarm)

    # 1) Management events — ~control-plane activity per day.
    day = window_start
    while day < now:
        n = rng.randint(8, 26)
        for _ in range(n):
            name, source, read_only, res_type, res_prefix = rng.choice(_MGMT)
            handle = rng.choice(handles)
            principal = _principal(ACCOUNT_ID, handle)
            service = rng.choice(services)
            when = day + timedelta(hours=rng.randint(0, 23), minutes=rng.randint(0, 59),
                                   seconds=rng.randint(0, 59))
            if when >= now:
                continue
            record, resources = _mgmt_record(
                rng, account=ACCOUNT_ID, region=REGION, principal=principal,
                name=name, source=source, read_only=read_only, res_type=res_type,
                res_prefix=res_prefix, service=service, when=when)
            rows.append((record["eventID"], _ms(when), name, source, REGION,
                         handle, principal["accessKeyId"], read_only,
                         json.dumps(resources), json.dumps(record), False))
        day += timedelta(days=1)

    # 2) CloudWatch alarm-state changes — firing→resolved pairs every ~2-3 days.
    day = window_start + timedelta(days=rng.randint(1, 3))
    while day < now - timedelta(hours=2):
        if rng.random() < 0.4:
            alarm = rng.choice(_ALARMS)
            service = rng.choice(services)
            fire = day + timedelta(hours=rng.randint(0, 23), minutes=rng.randint(0, 59))
            dur = rng.choice([7, 12, 19, 34, 58, 91])
            resolve = fire + timedelta(minutes=dur)
            for new_state, prev_state, when in (
                ("ALARM", "OK", fire),
                ("OK", "ALARM", resolve),
            ):
                if when >= now:
                    continue
                rec = _alarm_record(rng, account=ACCOUNT_ID, region=REGION,
                                    alarm=alarm, new_state=new_state,
                                    prev_state=prev_state, when=when, service=service)
                rows.append((rec["eventID"], _ms(when), rec["eventName"],
                             rec["eventSource"], REGION, "", "", False,
                             json.dumps([]), json.dumps(rec), True))
        day += timedelta(days=1)

    for r in rows:
        await pool.execute(
            """INSERT INTO app_aws.events
                (id, install_pk, event_id, event_time_ms, event_name, event_source,
                 aws_region, username, access_key_id, read_only, resources, record,
                 is_alarm, created_at, is_historical)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12::jsonb,$13,$14,TRUE)""",
            uuid4(), inst_pk, r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7],
            r[8], r[9], r[10],
            datetime.fromtimestamp(r[1] / 1000, tz=timezone.utc))

    alarms = sum(1 for r in rows if r[10])
    return {"events": len(rows), "alarms": alarms}
