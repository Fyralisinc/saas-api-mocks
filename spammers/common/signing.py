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

import binascii
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


# ---------- Ashby (recruiting webhook) ----------

def ashby_sign(secret: Union[str, bytes], body: Union[str, bytes]) -> str:
    """Return the value for Ashby's ``Ashby-Signature`` webhook header.

    Real Ashby: ``sha256=`` + lowercase-hex(HMAC-SHA256(secret, rawBody)). The
    ``sha256=`` prefix IS present (it names the digest algorithm) — unlike Grafana
    (bare hex) and Mercury (``t=…,v1=…``). Signed over the RAW request body alone,
    no timestamp / replay window. Same wire shape as GitHub's ``X-Hub-Signature-256``.
    """
    mac = hmac.new(_to_bytes(secret), _to_bytes(body), hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def ashby_verify(secret: Union[str, bytes], header: str, body: Union[str, bytes]) -> bool:
    expected = ashby_sign(secret, body)
    return hmac.compare_digest(expected, header or "")


# ---------- Brex (Svix-scheme webhook) ----------

def brex_sign(secret: Union[str, bytes], body: Union[str, bytes],
              *, msg_id: str, timestamp: Union[str, int]) -> str:
    """Return the value for Brex's ``Webhook-Signature`` header (Svix scheme).

    Real Brex webhooks are signed with **Svix's standard symmetric scheme**, with
    the headers renamed ``Webhook-Id`` / ``Webhook-Timestamp`` / ``Webhook-Signature``:

      signed content = ``"{Webhook-Id}.{Webhook-Timestamp}.{rawBody}"`` (literal dots)
      key            = base64-decode of the secret AFTER its ``whsec_`` prefix
      signature      = ``"v1," + base64(HMAC-SHA256(key, signedContent))``

    The header is a SPACE-delimited list of ``version,signature`` pairs (so a
    secret rotation can carry two). Base64 (NOT hex), ``v1,`` version tag (NOT
    ``sha256=``), and the timestamp lives in a SEPARATE header (NOT Stripe's
    ``t=,v1=``). Returns a single ``v1,<base64>`` token.
    """
    import base64
    raw_secret = secret.decode() if isinstance(secret, (bytes, bytearray)) else str(secret)
    key_b64 = raw_secret[len("whsec_"):] if raw_secret.startswith("whsec_") else raw_secret
    try:
        key = base64.b64decode(key_b64)
    except (ValueError, binascii.Error):
        key = _to_bytes(raw_secret)
    signed = f"{msg_id}.{timestamp}.".encode("ascii") + _to_bytes(body)
    mac = hmac.new(key, signed, hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode("ascii")


def brex_verify(secret: Union[str, bytes], header: str, body: Union[str, bytes],
                *, msg_id: str, timestamp: Union[str, int]) -> bool:
    """Constant-time verify of a Svix ``Webhook-Signature`` header.

    The header may carry MULTIPLE space-delimited ``v1,<base64>`` tokens (rotation);
    any one matching is a pass. Compares only the base64 portion after ``v1,``.
    """
    if not header:
        return False
    expected = brex_sign(secret, body, msg_id=msg_id, timestamp=timestamp).split(",", 1)[1]
    for token in header.split(" "):
        token = token.strip()
        if not token:
            continue
        version, _, sig = token.partition(",")
        if version == "v1" and hmac.compare_digest(expected, sig):
            return True
    return False


# ---------- Deel (global payroll webhook) ----------

def deel_sign(secret: Union[str, bytes], body: Union[str, bytes]) -> str:
    """Return the value for Deel's ``x-deel-signature`` webhook header.

    Real Deel: a **bare lowercase-hex** HMAC-SHA256 digest — NO ``sha256=`` prefix,
    NOT base64 — computed over the string **``"POST" + rawBody``** (the literal HTTP
    method ``POST`` prepended to the raw request body; there is NO timestamp in the
    signed string). The delivery also carries companion headers ``x-deel-hmac-label``
    (which signing key) and ``x-deel-webhook-version`` (serialization version) — those
    name/select the key, the signature itself is just this digest. (Contrast GitHub's
    ``sha256=<hex>`` over the body alone, Mercury's ``t=,v1=`` and Brex's Svix base64.)
    """
    signed = b"POST" + _to_bytes(body)
    return hmac.new(_to_bytes(secret), signed, hashlib.sha256).hexdigest()


def deel_verify(secret: Union[str, bytes], header: str, body: Union[str, bytes]) -> bool:
    expected = deel_sign(secret, body)
    return hmac.compare_digest(expected, (header or "").strip())


# ---------- HiBob (Bob HR webhook) ----------

def hibob_sign(secret: Union[str, bytes], body: Union[str, bytes]) -> str:
    """Return the value for HiBob's ``Bob-Signature`` webhook header.

    Real HiBob (apidocs.hibob.com/reference/getting-started-webhooks): a
    **base64-encoded HMAC-SHA512** digest computed over the **raw request body
    alone** — NOT SHA256, NOT hex, NO ``sha512=`` prefix, and NO timestamp in the
    signed bytes. The base64 is the plain digest with NO line breaks (the docs'
    JS/PHP ``digest("base64")`` form, not Ruby's newline-wrapping ``encode64``).
    (Contrast GitHub's ``sha256=<hex>``, Deel's bare-hex-over-"POST"+body, Brex's
    Svix.)
    """
    import base64 as _b64
    mac = hmac.new(_to_bytes(secret), _to_bytes(body), hashlib.sha512).digest()
    return _b64.b64encode(mac).decode("ascii")


def hibob_verify(secret: Union[str, bytes], header: str, body: Union[str, bytes]) -> bool:
    expected = hibob_sign(secret, body)
    return hmac.compare_digest(expected, (header or "").strip())


# ---------- Ramp (corporate-card / spend webhook) ----------

def ramp_sign(secret: Union[str, bytes], body: Union[str, bytes]) -> str:
    """Return the value for Ramp's ``X-Ramp-Signature`` webhook header.

    Real Ramp (docs.ramp.com/developer-api/v1/guides/webhooks): *"Every webhook
    request from Ramp includes an X-Ramp-Signature header, which contains an
    HMAC-SHA256 hash of the raw request body, signed with your webhook secret."*
    The sample value in the docs is **bare lowercase hex** — NO ``sha256=`` /
    ``v1,`` prefix, NOT base64 — over the **raw request body alone** (no timestamp,
    no method prefix). It is the simplest possible HMAC shape: GitHub's
    ``X-Hub-Signature-256`` minus the ``sha256=`` prefix. (Contrast GitHub's
    ``sha256=<hex>``, Deel's bare-hex over ``"POST"+body``, Brex's Svix base64,
    HiBob's base64-SHA512.) NB: this is the ramp.com spend-management Ramp, NOT
    "Ramp Network" the crypto on/off-ramp (which uses ECDSA + ``X-Body-Signature``).
    """
    return hmac.new(_to_bytes(secret), _to_bytes(body), hashlib.sha256).hexdigest()


def ramp_verify(secret: Union[str, bytes], header: str, body: Union[str, bytes]) -> bool:
    expected = ramp_sign(secret, body)
    return hmac.compare_digest(expected, (header or "").strip())


def gusto_sign(secret: Union[str, bytes], body: Union[str, bytes]) -> str:
    """Return the value for Gusto's ``X-Gusto-Signature`` webhook header.

    Real Gusto (docs.gusto.com/embedded-payroll/docs/webhooks): every webhook
    delivery carries an ``X-Gusto-Signature`` header = an HMAC-SHA256 of the raw
    request body, keyed by the webhook subscription's ``verification_token``. No
    ``sha256=`` prefix, no timestamp in the signed bytes (GitHub's
    ``X-Hub-Signature-256`` shape minus the prefix).

    NOTE — the digest ENCODING (hex vs base64) is the ONE wire detail Gusto does
    NOT document in prose, and the official auto-generated SDK ships no verifier
    to settle it. We default to **lowercase HEX** (the dominant convention for a
    bare single-header HMAC-SHA256: GitHub, Ramp, Deel all hex). This is the only
    INFERRED fact in the Gusto contract; it is a one-line switch if a real
    captured delivery proves base64. (Contrast: the Fyralis QBO-archetype clone
    assumes ``Gusto-Signature`` — NO ``X-`` prefix — + base64; see the
    gusto-fidelity-audit memory's logged divergence.)
    """
    return hmac.new(_to_bytes(secret), _to_bytes(body), hashlib.sha256).hexdigest()


def gusto_verify(secret: Union[str, bytes], header: str, body: Union[str, bytes]) -> bool:
    expected = gusto_sign(secret, body)
    return hmac.compare_digest(expected, (header or "").strip())


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
