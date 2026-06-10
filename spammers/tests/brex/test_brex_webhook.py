"""Hard-fail tests for Brex's webhook (the REAL Svix signature scheme).

Brex signs deliveries with Svix's standard symmetric scheme under renamed headers:
``Webhook-Signature: v1,<base64(HMAC-SHA256(key, "{Webhook-Id}.{Webhook-Timestamp}.{rawBody}"))>``
where ``key`` is the base64-decode of the ``whsec_…`` secret. Multiple
space-delimited ``v1,<sig>`` tokens can appear during a secret rotation; the
timestamp lives in a SEPARATE ``Webhook-Timestamp`` header (NOT Stripe's t=,v1=).
"""
from __future__ import annotations

import base64
import hashlib
import hmac

from spammers.brex.webhooks import build_event
from spammers.common.signing import brex_sign, brex_verify
from spammers.brex.dto import WEBHOOK_EVENT_TYPES

SECRET = "whsec_e/ETGy3XpsXyhQS7MKyfI3wG5wTGkq6MoNQpIWTdIyg="
MSG_ID = "msg_abc123"
TS = 1730419200


def test_signature_is_v1_base64_over_id_ts_body():
    body = b'{"event_type":"TRANSFER_PROCESSED","transfer_id":"trnsfr_x"}'
    header = brex_sign(SECRET, body, msg_id=MSG_ID, timestamp=TS)
    assert header.startswith("v1,")
    sig_b64 = header[len("v1,"):]
    # recompute independently: key = base64-decode of part after whsec_
    key = base64.b64decode(SECRET[len("whsec_"):])
    signed = f"{MSG_ID}.{TS}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    assert sig_b64 == expected
    # base64, not hex
    base64.b64decode(sig_b64)  # must decode


def test_verify_roundtrip_and_tamper():
    body = b'{"event_type":"TRANSFER_FAILED","transfer_id":"trnsfr_y"}'
    header = brex_sign(SECRET, body, msg_id=MSG_ID, timestamp=TS)
    assert brex_verify(SECRET, header, body, msg_id=MSG_ID, timestamp=TS)
    # tampered body fails
    assert not brex_verify(SECRET, header, body + b"x", msg_id=MSG_ID, timestamp=TS)
    # wrong timestamp fails (ts is part of the signed string)
    assert not brex_verify(SECRET, header, body, msg_id=MSG_ID, timestamp=TS + 1)
    # wrong msg_id fails
    assert not brex_verify(SECRET, header, body, msg_id="msg_other", timestamp=TS)


def test_verify_accepts_rotation_multisig():
    body = b'{"transfer_id":"trnsfr_z"}'
    good = brex_sign(SECRET, body, msg_id=MSG_ID, timestamp=TS)
    # a rotation header carries two space-delimited v1 tokens; one matches.
    multi = f"v1,{base64.b64encode(b'wrongsig').decode()} {good}"
    assert brex_verify(SECRET, multi, body, msg_id=MSG_ID, timestamp=TS)


def test_verify_rejects_missing_or_unversioned():
    body = b'{"transfer_id":"trnsfr_z"}'
    assert not brex_verify(SECRET, "", body, msg_id=MSG_ID, timestamp=TS)
    # a bare base64 with no v1, prefix is rejected
    bare = brex_sign(SECRET, body, msg_id=MSG_ID, timestamp=TS)[len("v1,"):]
    assert not brex_verify(SECRET, bare, body, msg_id=MSG_ID, timestamp=TS)


def test_build_event_envelope():
    ev = build_event(
        {"transfer_id": "trnsfr_1", "payment_type": "ACH",
         "event_type": "TRANSFER_PROCESSED"},
        company_id="cmp_x")
    assert set(ev) == {"event_type", "transfer_id", "payment_type", "return_for_id",
                       "company_id"}
    assert ev["event_type"] in WEBHOOK_EVENT_TYPES
    assert ev["transfer_id"] == "trnsfr_1"
    assert ev["company_id"] == "cmp_x"
    assert ev["return_for_id"] is None
