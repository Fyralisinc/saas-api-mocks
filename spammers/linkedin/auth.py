"""Auth + protocol-header gates for the LinkedIn mock.

LinkedIn's versioned ``/rest/`` Community Management surface gates every read on
THREE request headers (pinned from learn.microsoft.com/linkedin):

  * ``Authorization: Bearer <token>`` — OAuth 2.0 access token (3-legged member auth
    in production; the mock accepts any non-empty Bearer, single-tenant per run). A
    missing/blank token → **401** classic envelope.
  * ``Linkedin-Version: YYYYMM`` — REQUIRED on every versioned call; "the latest
    version is not applied by default". A MISSING header → **400** ``VERSION_MISSING``;
    an out-of-window / malformed version → **426** ``NONEXISTENT_VERSION``.
  * ``X-Restli-Protocol-Version: 2.0.0`` — the Rest.li protocol version. Expected on
    these endpoints; the mock accepts it (and tolerates its absence rather than
    silently switching to protocol 1.0 param-encoding — a faithful-enough relaxation).

The error envelope is LinkedIn's CLASSIC 3-key shape ``{message, serviceErrorCode,
status}`` (NOT google.rpc.Status, NOT a QBO Fault). The version errors additionally
carry a ``code`` string (``VERSION_MISSING`` / ``NONEXISTENT_VERSION``).
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import Request

# Active versioned-API window. LinkedIn supports a version ~12 months then sunsets
# it; we accept a generous recent window and reject older/malformed → 426.
_VERSION_RE = re.compile(r"^\d{6}$")
_VERSION_MIN = 202401            # anything before this is sunset (→ 426)
_VERSION_MAX = 209912            # generous ceiling (future-proof the frozen run)


def bearer(request: Request) -> Optional[str]:
    """Extract the non-empty Bearer access token, else None."""
    h = request.headers.get("authorization") or request.headers.get("Authorization")
    if not h:
        return None
    h = h.strip()
    if h.lower().startswith("bearer "):
        tok = h[7:].strip()
        return tok or None
    return None


def linkedin_version(request: Request) -> Optional[str]:
    """The ``Linkedin-Version`` header value (HTTP headers are case-insensitive)."""
    return request.headers.get("linkedin-version") or request.headers.get("LinkedIn-Version")


def version_ok(value: Optional[str]) -> bool:
    """A version is ACTIVE iff it is YYYYMM and within the supported window."""
    if not value or not _VERSION_RE.match(value):
        return False
    return _VERSION_MIN <= int(value) <= _VERSION_MAX
