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
