"""Slack request-signing fidelity: v0=HMAC-SHA256(secret, "v0:{ts}:{body}")."""
from __future__ import annotations

import hashlib
import hmac

from spammers.common.signing import slack_sign, slack_verify

SECRET = "abcdef0123456789abcdef0123456789"


def _expected(secret: str, ts: str, body: bytes) -> str:
    base = b"v0:" + ts.encode() + b":" + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def test_sign_matches_independent_computation():
    body = b'{"type":"event_callback"}'
    ts = "1700000000"
    assert slack_sign(SECRET, ts, body) == _expected(SECRET, ts, body)


def test_sign_has_v0_prefix():
    assert slack_sign(SECRET, "1", b"x").startswith("v0=")


def test_verify_roundtrip():
    body = b"payload-bytes"
    ts = "1700000123"
    sig = slack_sign(SECRET, ts, body)
    assert slack_verify(SECRET, sig, ts, body) is True


def test_verify_rejects_tampered_body():
    ts = "1700000123"
    sig = slack_sign(SECRET, ts, b"original")
    assert slack_verify(SECRET, sig, ts, b"tampered") is False


def test_verify_rejects_wrong_timestamp():
    sig = slack_sign(SECRET, "1700000123", b"body")
    assert slack_verify(SECRET, sig, "1700000999", b"body") is False
