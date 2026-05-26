"""Opaque page / sync token codecs for events.list.

Google's ``nextPageToken`` and ``nextSyncToken`` are opaque, self-contained
strings. We encode just enough state to be stateless: a page token carries an
offset; a sync token carries the high-water ``updated_at`` the next incremental
call reads strictly after.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Optional


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def encode_page_token(offset: int) -> str:
    return _b64u(json.dumps({"off": offset}).encode())


def decode_page_token(tok: str) -> Optional[int]:
    try:
        return int(json.loads(_b64u_decode(tok))["off"])
    except Exception:
        return None


def encode_sync_token(high_water: datetime) -> str:
    return _b64u(json.dumps({"hw": high_water.astimezone(timezone.utc).isoformat()}).encode())


def decode_sync_token(tok: str) -> Optional[datetime]:
    """Return the high-water datetime, or None if the token is unparseable
    (which the caller treats as an expired token → HTTP 410)."""
    try:
        raw = json.loads(_b64u_decode(tok))
        return datetime.fromisoformat(raw["hw"])
    except Exception:
        return None
