"""Ashby webhook fidelity — HMAC signing + the {action, data} envelope.

Audited vs developer.ashbyhq.com/docs/authenticating-webhooks:
  * Ashby-Signature = ``sha256=`` + lowercase-hex(HMAC-SHA256(secret, rawBody)) —
    the ``sha256=`` prefix IS present (it names the algorithm), over the RAW body,
    no timestamp / replay window (same wire shape as GitHub's X-Hub-Signature-256).
  * the delivery payload is ``{"action": "<eventType>", "data": {"<entity>": {…}}}``.
"""
from __future__ import annotations

from spammers.common.signing import ashby_sign, ashby_verify
from spammers.ashby.webhooks import build_event


def test_ashby_signature_has_sha256_prefix_over_raw_body():
    secret = "whsec_ashby"
    body = b'{"action":"applicationSubmit","data":{}}'
    sig = ashby_sign(secret, body)
    assert sig.startswith("sha256="), "the sha256= prefix IS present (names the algorithm)"
    hexpart = sig.split("=", 1)[1]
    assert len(hexpart) == 64 and all(c in "0123456789abcdef" for c in hexpart), "lowercase hex"
    assert ashby_verify(secret, sig, body)
    # tampered body / wrong secret must fail
    assert not ashby_verify(secret, sig, body + b"x")
    assert not ashby_verify("wrong", sig, body)
    # a bare hex digest with no prefix must NOT verify (Ashby requires the prefix)
    assert not ashby_verify(secret, hexpart, body)


def test_build_event_envelope_is_action_and_data():
    application = {"id": "a1", "status": "Active", "updatedAt": "2026-02-01T00:00:00.000Z"}
    payload = {"action": "applicationSubmit", "data": {"application": application}}
    ev = build_event(payload)
    assert set(ev.keys()) == {"action", "data"}
    assert ev["action"] == "applicationSubmit"
    assert ev["data"]["application"]["id"] == "a1"


def test_build_event_defaults_empty_data():
    ev = build_event({"action": "ping"})
    assert ev == {"action": "ping", "data": {}}
