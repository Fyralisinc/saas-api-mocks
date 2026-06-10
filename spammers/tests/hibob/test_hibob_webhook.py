"""Hard-fail tests for HiBob's webhook (the REAL ``Bob-Signature`` HMAC scheme).

HiBob signs deliveries with a **base64-encoded HMAC-SHA512** over the raw body
alone — NOT SHA256, NOT hex, NO ``sha512=`` prefix, NO timestamp. The Webhooks-v2
payload is the metadata-only envelope ``{companyId, type, triggeredBy, triggeredAt,
version, data}`` (``data`` carries IDs / field-update ids, NOT the full object).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timezone

from spammers.common.signing import hibob_sign, hibob_verify
from spammers.hibob.webhooks import build_event
from spammers.hibob.dto import WEBHOOK_EVENT_TYPES

SECRET = "bobhk_3f9c1e7a52b84d06c1a9f4e2d7b605c8e3a0f1d6b9c2e5a8f4d7b0c3e6a9f2d5"


def test_signature_is_base64_hmac_sha512_over_body():
    body = b'{"companyId":636192,"type":"employee.updated"}'
    header = hibob_sign(SECRET, body)
    # bare base64 (no sha512= prefix, no hex), no embedded newline.
    assert not header.startswith("sha512=")
    assert "\n" not in header
    # base64 decodes to a 64-byte SHA-512 digest.
    assert len(base64.b64decode(header)) == 64
    # recompute independently: base64(HMAC-SHA512(secret, body))
    expected = base64.b64encode(
        hmac.new(SECRET.encode(), body, hashlib.sha512).digest()).decode()
    assert header == expected
    # it is genuinely SHA-512, NOT SHA-256 (a sha256 digest would be 32 bytes).
    sha256_b64 = base64.b64encode(
        hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()
    assert header != sha256_b64


def test_verify_roundtrip_and_tamper():
    body = b'{"companyId":636192,"type":"employee.updated","data":{}}'
    header = hibob_sign(SECRET, body)
    assert hibob_verify(SECRET, header, body)
    # tampered body fails
    assert not hibob_verify(SECRET, header, body + b"x")
    # a hex digest (wrong encoding) is rejected
    hexsig = hmac.new(SECRET.encode(), body, hashlib.sha512).hexdigest()
    assert not hibob_verify(SECRET, hexsig, body)
    # empty/garbage header fails
    assert not hibob_verify(SECRET, "", body)


def test_build_event_v2_metadata_only_envelope():
    occurred = datetime(2024, 12, 30, 12, 56, 18, 955603, tzinfo=timezone.utc)
    payload = {"event_type": "employee.updated", "triggered_by": "1001",
               "data": {"employeeId": "1001", "fieldUpdatesIds": [{"id": "root.surname"}]}}
    ev = build_event(payload, company_id="636192", occurred=occurred)
    assert set(ev) == {"companyId", "type", "triggeredBy", "triggeredAt", "version", "data"}
    # companyId is a NUMBER on the wire (not a string).
    assert ev["companyId"] == 636192 and isinstance(ev["companyId"], int)
    assert ev["type"] in WEBHOOK_EVENT_TYPES
    assert ev["version"] == "v2"
    assert ev["triggeredBy"] == "1001"
    # triggeredAt is ISO-8601 microseconds with NO Z.
    assert ev["triggeredAt"] == "2024-12-30T12:56:18.955603"
    # data is metadata-only (IDs + field-update ids), NOT a full employee object.
    assert ev["data"]["employeeId"] == "1001"
    assert ev["data"]["fieldUpdatesIds"] == [{"id": "root.surname"}]
