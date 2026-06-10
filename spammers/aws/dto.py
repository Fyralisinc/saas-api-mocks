"""Wire shapes for the AWS mock.

Two protocols:
  * **CloudTrail (AWS JSON 1.1):** the ``LookupEvents`` response is
    ``{"Events":[<Event>…], "NextToken":"…"}``. Each ``Event`` is the wrapper
    (``EventId``/``EventName``/``ReadOnly``/``AccessKeyId``/``EventTime``/
    ``EventSource``/``Username``/``Resources``/``CloudTrailEvent``) — note
    ``ReadOnly`` is a **string** ``"true"``/``"false"`` (NOT a bool), ``EventTime``
    is **epoch SECONDS as a number** (the JSON-1.1 timestamp encoding), and
    ``CloudTrailEvent`` is a **JSON-encoded string** of the native record.
  * **STS (AWS Query):** XML responses for ``GetCallerIdentity`` / ``AssumeRole``,
    and the ``ErrorResponse`` envelope.

Errors:
  * CloudTrail (JSON 1.1): ``{"__type":"<Name>","message":"…"}`` + an
    ``x-amzn-ErrorType`` header. botocore keys the exception on ``__type``.
  * STS (Query): ``<ErrorResponse><Error><Type/><Code/><Message/></Error>
    <RequestId/></ErrorResponse>``; botocore keys on ``<Code>``.
"""
from __future__ import annotations

import json
from typing import Any
from xml.sax.saxutils import escape

STS_XMLNS = "https://sts.amazonaws.com/doc/2011-06-15/"


def _as_list(value: Any) -> list:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return []
    return value or []


def lookup_event_dto(row: dict) -> dict[str, Any]:
    """Project an ``app_aws.events`` row into a LookupEvents ``Event`` wrapper.

    Omits ``Username``/``AccessKeyId`` when empty (real CloudTrail omits them for
    service/machine events); ``ReadOnly`` is always a string; ``EventTime`` is
    epoch seconds; ``CloudTrailEvent`` is the native record serialized to a string.
    """
    record = row["record"]
    if isinstance(record, str):
        record = json.loads(record)
    out: dict[str, Any] = {
        "EventId": row["event_id"],
        "EventName": row["event_name"],
        "ReadOnly": "true" if row["read_only"] else "false",
        "EventTime": int(int(row["event_time_ms"]) // 1000),
        "EventSource": row["event_source"],
        "Resources": _as_list(row.get("resources")),
        "CloudTrailEvent": json.dumps(record, separators=(",", ":")),
    }
    if row.get("username"):
        out["Username"] = row["username"]
    if row.get("access_key_id"):
        out["AccessKeyId"] = row["access_key_id"]
    return out


def lookup_events_response(events: list[dict], next_token: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {"Events": [lookup_event_dto(e) for e in events]}
    if next_token:
        body["NextToken"] = next_token
    return body


# --------------------------------------------------------------------------- STS XML


def _el(tag: str, text: str) -> str:
    return f"<{tag}>{escape(str(text))}</{tag}>"


def get_caller_identity_xml(*, arn: str, user_id: str, account: str,
                            request_id: str) -> str:
    return (
        f'<GetCallerIdentityResponse xmlns="{STS_XMLNS}">'
        "<GetCallerIdentityResult>"
        f"{_el('Arn', arn)}{_el('UserId', user_id)}{_el('Account', account)}"
        "</GetCallerIdentityResult>"
        f"<ResponseMetadata>{_el('RequestId', request_id)}</ResponseMetadata>"
        "</GetCallerIdentityResponse>"
    )


def assume_role_xml(*, access_key_id: str, secret_access_key: str,
                    session_token: str, expiration_iso: str,
                    assumed_role_id: str, assumed_role_arn: str,
                    packed_policy_size: int, request_id: str) -> str:
    return (
        f'<AssumeRoleResponse xmlns="{STS_XMLNS}">'
        "<AssumeRoleResult>"
        "<Credentials>"
        f"{_el('AccessKeyId', access_key_id)}"
        f"{_el('SecretAccessKey', secret_access_key)}"
        f"{_el('SessionToken', session_token)}"
        f"{_el('Expiration', expiration_iso)}"
        "</Credentials>"
        "<AssumedRoleUser>"
        f"{_el('AssumedRoleId', assumed_role_id)}"
        f"{_el('Arn', assumed_role_arn)}"
        "</AssumedRoleUser>"
        f"{_el('PackedPolicySize', packed_policy_size)}"
        "</AssumeRoleResult>"
        f"<ResponseMetadata>{_el('RequestId', request_id)}</ResponseMetadata>"
        "</AssumeRoleResponse>"
    )


def sts_error_xml(*, code: str, message: str, request_id: str,
                  fault: str = "Sender") -> str:
    return (
        f'<ErrorResponse xmlns="{STS_XMLNS}">'
        "<Error>"
        f"{_el('Type', fault)}{_el('Code', code)}{_el('Message', message)}"
        "</Error>"
        f"{_el('RequestId', request_id)}"
        "</ErrorResponse>"
    )


def cloudtrail_error(code: str, message: str) -> dict[str, Any]:
    """JSON 1.1 error body. botocore reads the exception name from ``__type``."""
    return {"__type": code, "message": message}
