"""Hard-fail tests for Gusto's webhook (the REAL X-Gusto-Signature scheme).

Gusto signs deliveries with a single header carrying a **lowercase-hex
HMAC-SHA256 of the raw request body**, keyed by the subscription's
``verification_token`` — NO ``sha256=`` prefix, NO timestamp in the signed bytes
(GitHub's ``X-Hub-Signature-256`` shape minus the prefix). The event is THIN —
``{uuid, event_type, resource_type, resource_uuid, entity_type, entity_uuid,
timestamp}`` carries only references, and ``timestamp`` is a numeric Unix EPOCH.

(The Fyralis QBO-archetype clone wrongly assumes ``Gusto-Signature`` — no ``X-``
prefix — + base64; logged in the gusto-fidelity-audit memory. The hex-vs-base64
encoding is the one INFERRED contract detail.)
"""
from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timezone

from spammers.common.signing import gusto_sign, gusto_verify
from spammers.gusto.webhooks import build_event
from spammers.gusto.dto import WEBHOOK_EVENT_TYPES

SECRET = "gwhv_fidelity00000000000000000000000000"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def test_signature_is_bare_lowercase_hex_no_prefix():
    body = b'{"uuid":"e1","event_type":"payroll.processed","resource_uuid":"p1"}'
    sig = gusto_sign(SECRET, body)
    assert _HEX64.match(sig), sig
    assert "=" not in sig and "," not in sig and " " not in sig
    expected = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert sig == expected


def test_verify_roundtrip_and_tamper():
    body = b'{"uuid":"e2","event_type":"employee.updated"}'
    sig = gusto_sign(SECRET, body)
    assert gusto_verify(SECRET, sig, body)
    assert not gusto_verify(SECRET, sig, body + b"x")      # tampered body
    assert not gusto_verify("gwhv_other", sig, body)       # wrong secret
    assert not gusto_verify(SECRET, "", body)              # empty header


def test_build_event_thin_envelope_epoch_timestamp():
    ev = build_event(
        {"resource_type": "Payroll",
         "resource_uuid": "p1000000-0000-4000-8000-000000000001",
         "event_type": "payroll.processed"},
        company_uuid="a1b2c3d4-5e6f-4a7b-8c9d-000000000001",
        event_id="11111111-1111-4111-8111-111111111111",
        occurred=datetime(2026, 2, 1, 12, 0, 0, tzinfo=timezone.utc))
    assert set(ev) == {"uuid", "event_type", "resource_type", "resource_uuid",
                       "entity_type", "entity_uuid", "timestamp"}
    assert ev["event_type"] in WEBHOOK_EVENT_TYPES
    assert ev["resource_type"] == "Payroll"
    assert ev["entity_type"] == "Company"
    assert ev["entity_uuid"] == "a1b2c3d4-5e6f-4a7b-8c9d-000000000001"
    assert ev["resource_uuid"] == "p1000000-0000-4000-8000-000000000001"
    # timestamp is a numeric Unix EPOCH (NOT an ISO string)
    assert isinstance(ev["timestamp"], int)
    assert ev["timestamp"] == int(datetime(2026, 2, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp())


def test_employee_event_resolves_entity_company():
    ev = build_event(
        {"resource_type": "Employee", "resource_uuid": "emp-1",
         "event_type": "employee.updated"},
        company_uuid="co-1", event_id="22222222-2222-4222-8222-222222222222",
        occurred=datetime(2026, 2, 1, tzinfo=timezone.utc))
    assert ev["resource_type"] == "Employee"
    assert ev["entity_type"] == "Company" and ev["entity_uuid"] == "co-1"
