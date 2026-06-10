"""AWS Signature Version 4 verification for the mock.

botocore SigV4-signs every request it sends to this server (it has no idea it's a
mock — it's pointed here via an ``endpoint_url`` override). The mock RECOMPUTES
the signature from the request bytes against the seeded secret-access-key and
rejects a mismatch — the faithful failure mode (403 ``SignatureDoesNotMatch``)
that a tampered request would hit against real AWS.

The algorithm (per the official "Create a signed AWS API request" reference):

  1. Parse ``Authorization: AWS4-HMAC-SHA256 Credential=<ak>/<date>/<region>/
     <service>/aws4_request, SignedHeaders=<h1;h2;…>, Signature=<hex>``.
  2. Canonical request = METHOD\\nURI\\nQUERY\\nCANONICAL_HEADERS\\n\\n
     SIGNED_HEADERS\\nHEX(SHA256(body)).
  3. String to sign = "AWS4-HMAC-SHA256\\n" + amzdate + "\\n" + scope + "\\n" +
     HEX(SHA256(canonical_request)).
  4. kSigning = HMAC(HMAC(HMAC(HMAC("AWS4"+secret, date), region), service),
     "aws4_request"); signature = HEX(HMAC(kSigning, string_to_sign)).

Only the headers named in ``SignedHeaders`` are folded into the canonical request
(robust against whatever extra headers a proxy adds). For non-S3 services botocore
inlines ``HEX(SHA256(body))`` directly (no ``x-amz-content-sha256`` header, no
``UNSIGNED-PAYLOAD``). ``user-agent`` is never signed.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Optional

# In-process store of temp credentials minted by STS:AssumeRole (ASIA… keys).
# {access_key_id: TempCredential}. The mock is single-process per run, so an
# AssumeRole immediately followed by a signed call (the slice's assume-role
# exercise) resolves here.
_TEMP_CREDS: dict[str, "TempCredential"] = {}


@dataclass
class TempCredential:
    access_key_id: str
    secret_access_key: str
    session_token: str
    expiration_ms: int
    account_id: str
    assumed_role_arn: str
    assumed_role_id: str


def remember_temp_credential(cred: TempCredential) -> None:
    _TEMP_CREDS[cred.access_key_id] = cred


def temp_credential(access_key_id: str) -> Optional[TempCredential]:
    return _TEMP_CREDS.get(access_key_id)


@dataclass
class SigV4Auth:
    access_key_id: str
    date: str            # YYYYMMDD (datestamp)
    region: str
    service: str
    signed_headers: list[str]
    signature: str
    amz_date: str        # the x-amz-date header value (ISO basic, e.g. 20260610T...Z)


def parse_authorization(authorization: str | None, amz_date: str | None) -> Optional[SigV4Auth]:
    """Parse a SigV4 ``Authorization`` header. Returns None if not a SigV4 header."""
    if not authorization or not authorization.startswith("AWS4-HMAC-SHA256 "):
        return None
    rest = authorization[len("AWS4-HMAC-SHA256 "):]
    parts = {}
    for piece in rest.split(","):
        piece = piece.strip()
        if "=" not in piece:
            continue
        k, v = piece.split("=", 1)
        parts[k.strip()] = v.strip()
    cred = parts.get("Credential")
    signed = parts.get("SignedHeaders")
    sig = parts.get("Signature")
    if not (cred and signed and sig):
        return None
    scope = cred.split("/")
    if len(scope) != 5 or scope[4] != "aws4_request":
        return None
    ak, date, region, service, _ = scope
    return SigV4Auth(
        access_key_id=ak, date=date, region=region, service=service,
        signed_headers=[h for h in signed.split(";") if h],
        signature=sig, amz_date=amz_date or "",
    )


def _trim(value: str) -> str:
    # SigV4 "trimall": strip + collapse sequential internal spaces to one.
    return " ".join(value.split())


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, date: str, region: str, service: str) -> bytes:
    k_date = _sign(("AWS4" + secret).encode("utf-8"), date)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    return _sign(k_service, "aws4_request")


def expected_signature(
    auth: SigV4Auth,
    *,
    method: str,
    path: str,
    query_string: str,
    headers: dict[str, str],
    body: bytes,
    secret_access_key: str,
) -> str:
    """Recompute the SigV4 hex signature for a request. ``headers`` is a
    lowercased-name → value map of (at least) every header in SignedHeaders."""
    # 1) Canonical request.
    canonical_headers = ""
    for name in auth.signed_headers:  # already lowercase, in the signed order (sorted)
        canonical_headers += f"{name}:{_trim(headers.get(name, ''))}\n"
    signed_headers = ";".join(auth.signed_headers)
    payload_hash = hashlib.sha256(body or b"").hexdigest()
    canonical_request = "\n".join([
        method.upper(), path, query_string,
        canonical_headers, signed_headers, payload_hash,
    ])
    # 2) String to sign.
    scope = f"{auth.date}/{auth.region}/{auth.service}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", auth.amz_date, scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    # 3) Sign.
    key = _signing_key(secret_access_key, auth.date, auth.region, auth.service)
    return hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()


def verify(
    auth: SigV4Auth,
    *,
    method: str,
    path: str,
    query_string: str,
    headers: dict[str, str],
    body: bytes,
    secret_access_key: str,
) -> bool:
    """Constant-time check that the request's Signature matches a recompute."""
    expected = expected_signature(
        auth, method=method, path=path, query_string=query_string,
        headers=headers, body=body, secret_access_key=secret_access_key,
    )
    return hmac.compare_digest(expected, auth.signature)
