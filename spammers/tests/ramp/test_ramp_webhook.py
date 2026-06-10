"""Hard-fail tests for Ramp's webhook (the REAL X-Ramp-Signature scheme).

Ramp signs deliveries with a single header carrying a **bare lowercase-hex
HMAC-SHA256 of the raw request body** — NO ``sha256=`` / ``v1,`` prefix, NOT
base64, NO timestamp in the signed bytes (the simplest HMAC shape: GitHub's
``X-Hub-Signature-256`` minus the prefix). The event is THIN — ``{id, type,
created_at, business_id, object:{id}}`` carries only the resource id.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timezone

from spammers.common.signing import ramp_sign, ramp_verify
from spammers.ramp.webhooks import build_event
from spammers.ramp.dto import WEBHOOK_EVENT_TYPES

SECRET = "rwhs_fidelity00000000000000000000000000"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def test_signature_is_bare_lowercase_hex_no_prefix():
    body = b'{"id":"wbhe_x","type":"transactions.cleared","object":{"id":"t1"}}'
    sig = ramp_sign(SECRET, body)
    # bare 64-char lowercase hex — no sha256=, no v1, no base64
    assert _HEX64.match(sig), sig
    assert "=" not in sig and "," not in sig and " " not in sig
    expected = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert sig == expected


def test_verify_roundtrip_and_tamper():
    body = b'{"id":"wbhe_y","type":"transactions.declined"}'
    sig = ramp_sign(SECRET, body)
    assert ramp_verify(SECRET, sig, body)
    # tampered body fails
    assert not ramp_verify(SECRET, sig, body + b"x")
    # wrong secret fails
    assert not ramp_verify("rwhs_other", sig, body)
    # empty header fails
    assert not ramp_verify(SECRET, "", body)


def test_build_event_thin_envelope():
    ev = build_event(
        {"txn_id": "c1111111-1111-4111-8111-111111111111",
         "event_type": "transactions.cleared"},
        business_id="biz-1", event_id="wbhe_abc",
        created_at=datetime(2026, 2, 1, 12, 0, 0, tzinfo=timezone.utc))
    assert set(ev) == {"id", "type", "created_at", "business_id", "object"}
    assert ev["type"] in WEBHOOK_EVENT_TYPES
    assert ev["business_id"] == "biz-1"
    assert ev["id"] == "wbhe_abc"
    # the event is THIN — object carries only the resource id
    assert ev["object"] == {"id": "c1111111-1111-4111-8111-111111111111"}
    # created_at is ISO-8601 with a +00:00 offset
    assert ev["created_at"].endswith("+00:00")
