"""HTTP Basic auth for the HiBob mock.

HiBob authenticates every Public-API call with a **service user** credential
presented as HTTP Basic, where the username is the service-user id and the
password is the service-user token:

    Authorization: Basic base64("{service_user_id}:{token}")

(apidocs.hibob.com/reference/authorization — the literal example is
``Base64.encode(SERVICE-USER-ID:<token>)``). As of 2024-10-31 the older
API-access-token method is gone; service users are the only scheme — there is
**no Bearer form** (a Bearer header is not the HiBob scheme → unauthenticated).

The mock is single-tenant per run: it does not match a provisioned credential,
it accepts any Basic header whose decoded value has BOTH a non-empty username
and a non-empty password (the ``id:token`` shape), and rejects a missing/blank
or malformed one with HTTP **401**. (HiBob returns 401 for a failed
authentication and 403 for an authenticated-but-unauthorized scope; since the
mock accepts any well-formed credential, only the 401 path is exercised.)
"""
from __future__ import annotations

import base64
import binascii
from typing import Optional


def service_user(request) -> Optional[tuple[str, str]]:
    """Return ``(service_user_id, token)`` from the Basic header, else None.

    Both halves must be non-empty (HiBob's ``id:token`` shape). Bearer is
    deliberately NOT accepted — HiBob is Basic-only.
    """
    h = request.headers.get("authorization") or request.headers.get("Authorization")
    if not h:
        return None
    h = h.strip()
    if not h.lower().startswith("basic "):
        return None
    raw = h[6:].strip()
    try:
        decoded = base64.b64decode(raw).decode("utf-8", "replace")
    except (binascii.Error, ValueError):
        return None
    if ":" not in decoded:
        return None
    user, token = decoded.split(":", 1)
    if not user or not token:
        return None
    return user, token


def is_authed(request) -> bool:
    return service_user(request) is not None
