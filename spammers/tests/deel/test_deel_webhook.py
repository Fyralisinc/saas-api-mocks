"""Hard-fail tests for Deel's webhook (the REAL ``x-deel-signature`` HMAC scheme).

Deel signs deliveries with a **bare lowercase-hex** HMAC-SHA256 over the string
``"POST" + rawBody`` (the literal method string prepended; NO ``sha256=`` prefix,
NOT base64, NO timestamp). The payload is the nested envelope
``{data:{meta:{event_type,organization_id}, resource:[…]}, timestamp}``.
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone

from spammers.common.signing import deel_sign, deel_verify
from spammers.deel.webhooks import build_event
from spammers.deel.dto import WEBHOOK_EVENT_TYPES

SECRET = "whk_3f9c1e7a52b84d06c1a9f4e2d7b605c8e3a0f1d6b9c2e5a8f4d7b0c3e6a9f2d5"


def test_signature_is_bare_hex_over_POST_plus_body():
    body = b'{"data":{"meta":{"event_type":"invoice.paid"}}}'
    header = deel_sign(SECRET, body)
    # bare lowercase hex, NO sha256= prefix
    assert not header.startswith("sha256=")
    assert all(ch in "0123456789abcdef" for ch in header)
    # recompute independently: HMAC over "POST" + body
    expected = hmac.new(SECRET.encode(), b"POST" + body, hashlib.sha256).hexdigest()
    assert header == expected
    # the "POST" prefix is load-bearing: signing the body ALONE differs
    body_only = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert header != body_only


def test_verify_roundtrip_and_tamper():
    body = b'{"data":{"meta":{"event_type":"contract.updated"}},"timestamp":"x"}'
    header = deel_sign(SECRET, body)
    assert deel_verify(SECRET, header, body)
    # tampered body fails
    assert not deel_verify(SECRET, header, body + b"x")
    # a body-only signature (missing the "POST" prefix) is rejected
    body_only = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert not deel_verify(SECRET, body_only, body)
    # empty/garbage header fails
    assert not deel_verify(SECRET, "", body)


def test_build_event_nested_envelope():
    resource = {"id": "inv_1", "status": "paid", "contract_id": "ctr_1"}
    occurred = datetime(2025, 2, 5, 15, 39, 38, 70_000, tzinfo=timezone.utc)
    ev = build_event({"event_type": "invoice.paid", "resource": resource},
                     organization_id="org_x", occurred=occurred)
    assert set(ev) == {"data", "timestamp"}
    assert set(ev["data"]) == {"meta", "resource"}
    assert set(ev["data"]["meta"]) == {"event_type", "organization_id"}
    assert ev["data"]["meta"]["event_type"] in WEBHOOK_EVENT_TYPES
    assert ev["data"]["meta"]["organization_id"] == "org_x"
    # resource is an ARRAY (even for a single object)
    assert ev["data"]["resource"] == [resource]
    # timestamp is RFC3339 with milliseconds + Z
    assert ev["timestamp"] == "2025-02-05T15:39:38.070Z"
