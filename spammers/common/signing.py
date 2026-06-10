"""Signature helpers for the four providers.

- ``slack_sign(secret, ts, body)`` → ``v0=<hex>`` (HMAC-SHA256)
- ``slack_verify(secret, header, ts, body)`` → bool
- ``github_sign(secret, body)`` → ``sha256=<hex>`` (HMAC-SHA256)
- ``github_verify(secret, header, body)`` → bool
- ``discord_ed25519_sign(private_key_hex, ts, body)`` → hex
- ``discord_ed25519_verify(public_key_hex, header, ts, body)`` → bool

Real-provider-compatible byte concatenations:
  Slack: signs ``"v0:" + ts + ":" + body`` (RFC 7515 §3 not applicable)
  GitHub: signs ``body`` directly with the app-level webhook secret
  Discord: signs ``ts + body`` with the app's ed25519 private key
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Union

try:
    from nacl.signing import SigningKey, VerifyKey  # type: ignore
    from nacl.exceptions import BadSignatureError  # type: ignore
except Exception:  # pragma: no cover — pynacl is in pyproject; this branch is for tooling
    SigningKey = None  # type: ignore
    VerifyKey = None  # type: ignore

    class BadSignatureError(Exception):  # type: ignore
        ...


BytesLike = Union[bytes, bytearray, memoryview]


def _to_bytes(x: Union[str, BytesLike]) -> bytes:
    if isinstance(x, str):
        return x.encode("utf-8")
    if isinstance(x, (bytearray, memoryview)):
        return bytes(x)
    return x


# ---------- Slack ----------

def slack_sign(secret: Union[str, bytes], ts: Union[str, int], body: Union[str, bytes]) -> str:
    """Return the value to put in the ``X-Slack-Signature`` header.

    Real Slack: ``v0=`` + hex(HMAC-SHA256(secret, ``"v0:{ts}:{body}"``))
    """
    secret_b = _to_bytes(secret)
    body_b = _to_bytes(body)
    base = b"v0:" + str(ts).encode("ascii") + b":" + body_b
    mac = hmac.new(secret_b, base, hashlib.sha256).hexdigest()
    return f"v0={mac}"


def slack_verify(
    secret: Union[str, bytes],
    header: str,
    ts: Union[str, int],
    body: Union[str, bytes],
) -> bool:
    expected = slack_sign(secret, ts, body)
    return hmac.compare_digest(expected, header or "")


# ---------- GitHub ----------

def github_sign(secret: Union[str, bytes], body: Union[str, bytes]) -> str:
    """Return the value for ``X-Hub-Signature-256``.

    Real GitHub: ``sha256=`` + hex(HMAC-SHA256(secret, body))
    """
    mac = hmac.new(_to_bytes(secret), _to_bytes(body), hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def github_verify(secret: Union[str, bytes], header: str, body: Union[str, bytes]) -> bool:
    expected = github_sign(secret, body)
    return hmac.compare_digest(expected, header or "")


# ---------- Intuit / QuickBooks Online ----------

def intuit_sign(verifier_token: Union[str, bytes], body: Union[str, bytes]) -> str:
    """Return the value for QBO's ``intuit-signature`` webhook header.

    Real Intuit: **base64**(HMAC-SHA256(verifierToken, rawBody)) — base64, NOT hex,
    and with no algorithm prefix.
    """
    import base64
    mac = hmac.new(_to_bytes(verifier_token), _to_bytes(body), hashlib.sha256).digest()
    return base64.b64encode(mac).decode("ascii")


def intuit_verify(verifier_token: Union[str, bytes], header: str,
                  body: Union[str, bytes]) -> bool:
    expected = intuit_sign(verifier_token, body)
    return hmac.compare_digest(expected, header or "")


# ---------- Grafana (Alerting webhook) ----------

def grafana_sign(secret: Union[str, bytes], body: Union[str, bytes],
                 *, timestamp: Union[str, int, None] = None) -> str:
    """Return the value for Grafana's ``X-Grafana-Alerting-Signature`` header.

    Real Grafana 12.0+: a **bare lowercase hex** HMAC-SHA256 digest with **no
    ``sha256=`` prefix** (unlike GitHub/Jira). By default the signed bytes are the
    raw request body alone; if the contact point is configured with a timestamp
    header, Grafana instead signs ``"{unix_ts}:" + body`` (off by default).
    """
    body_b = _to_bytes(body)
    signed = (str(timestamp).encode("ascii") + b":" + body_b
              if timestamp is not None else body_b)
    return hmac.new(_to_bytes(secret), signed, hashlib.sha256).hexdigest()


def grafana_verify(secret: Union[str, bytes], header: str, body: Union[str, bytes],
                   *, timestamp: Union[str, int, None] = None) -> bool:
    expected = grafana_sign(secret, body, timestamp=timestamp)
    return hmac.compare_digest(expected, header or "")


# ---------- Mercury (transaction webhook) ----------

def mercury_sign(secret: Union[str, bytes], body: Union[str, bytes],
                 timestamp: Union[str, int]) -> str:
    """Return the value for Mercury's ``Mercury-Signature`` webhook header.

    Real Mercury: a Stripe-style ``t=<unix_seconds>,v1=<hex>`` pair where the hex
    is HMAC-SHA256 over the string ``"{timestamp}.{rawBody}"`` (the unix-seconds
    timestamp, a literal ``.``, then the raw request body). Bare lowercase hex —
    NO ``sha256=`` prefix, NOT base64.
    """
    ts = str(timestamp)
    signed = ts.encode("ascii") + b"." + _to_bytes(body)
    mac = hmac.new(_to_bytes(secret), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def mercury_verify(secret: Union[str, bytes], header: str,
                   body: Union[str, bytes]) -> bool:
    """Constant-time verify of a ``t=…,v1=…`` Mercury-Signature header.

    Parses the timestamp out of the header, recomputes the digest over
    ``"{t}.{body}"`` and compares the ``v1`` value. (Replay-window enforcement is
    the receiver's job — Mercury recommends rejecting timestamps older than 5m.)
    """
    if not header:
        return False
    parts = {}
    for seg in header.split(","):
        if "=" in seg:
            k, v = seg.split("=", 1)
            parts[k.strip()] = v.strip()
    ts, got = parts.get("t"), parts.get("v1")
    if not ts or not got:
        return False
    expected = mercury_sign(secret, body, ts)
    return hmac.compare_digest(expected.split("v1=", 1)[1], got)


def github_sign_sha1(secret: Union[str, bytes], body: Union[str, bytes]) -> str:
    """Return the value for the legacy ``X-Hub-Signature`` header.

    Real GitHub still sends the SHA-1 signature alongside ``X-Hub-Signature-256``
    on every delivery when a webhook secret is configured:
    ``sha1=`` + hex(HMAC-SHA1(secret, body)).
    """
    mac = hmac.new(_to_bytes(secret), _to_bytes(body), hashlib.sha1).hexdigest()  # noqa: S324
    return f"sha1={mac}"


def generate_rsa_keypair(key_size: int = 2048) -> tuple[str, str]:
    """Return ``(private_pem, public_pem)`` for a GitHub App.

    The consumer signs App JWTs with the private key (RS256); the mock verifies
    them with the public key.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


# ---------- Discord (Ed25519) ----------

def generate_ed25519_keypair() -> tuple[str, str]:
    """Return ``(private_key_hex, public_key_hex)`` for a Discord application.

    The mock signs interaction webhooks with the private key; the consumer
    verifies ``X-Signature-Ed25519`` against the public key (the value a real
    app copies from the Developer Portal).
    """
    if SigningKey is None:  # pragma: no cover — pynacl is in pyproject
        raise RuntimeError("pynacl not installed")
    sk = SigningKey.generate()
    private_hex = bytes(sk).hex()
    public_hex = bytes(sk.verify_key).hex()
    return private_hex, public_hex


def discord_sign(
    private_key_hex: str,
    ts: Union[str, int],
    body: Union[str, bytes],
) -> str:
    """Return the hex signature for ``X-Signature-Ed25519``.

    Real Discord: Ed25519 signature over ``ts.encode + body.encode``.
    """
    if SigningKey is None:  # pragma: no cover
        raise RuntimeError("pynacl not installed")
    sk = SigningKey(bytes.fromhex(private_key_hex))
    msg = str(ts).encode("ascii") + _to_bytes(body)
    sig = sk.sign(msg).signature
    return sig.hex()


def discord_verify(
    public_key_hex: str,
    header: str,
    ts: Union[str, int],
    body: Union[str, bytes],
) -> bool:
    if VerifyKey is None:  # pragma: no cover
        raise RuntimeError("pynacl not installed")
    try:
        vk = VerifyKey(bytes.fromhex(public_key_hex))
        msg = str(ts).encode("ascii") + _to_bytes(body)
        vk.verify(msg, bytes.fromhex(header))
        return True
    except (BadSignatureError, ValueError):
        return False
