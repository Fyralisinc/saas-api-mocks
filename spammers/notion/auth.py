"""Bearer-token auth + opaque cursor codec for the Notion mock."""
from __future__ import annotations

import base64
import json
from typing import Optional

from fastapi import Request

from spammers.notion.state import state


def bearer(request: Request) -> Optional[str]:
    h = request.headers.get("authorization") or request.headers.get("Authorization")
    if not h:
        return None
    h = h.strip()
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    return h


def authed(request: Request) -> bool:
    """True iff the request carries this run's integration bot token."""
    tok = bearer(request)
    return bool(tok) and tok == state().bot_token


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"o": offset}).encode()).decode().rstrip("=")


def decode_cursor(cursor: Optional[str]) -> int:
    if not cursor:
        return 0
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        return int(json.loads(raw)["o"])
    except Exception:
        return 0


def page_slice(items: list, start_cursor: Optional[str], page_size: Optional[int]):
    """Return ``(page_items, next_cursor)`` for Notion's offset-style cursors."""
    size = 100 if not page_size else max(1, min(int(page_size), 100))
    offset = decode_cursor(start_cursor)
    page = items[offset:offset + size]
    next_cursor = encode_cursor(offset + size) if offset + size < len(items) else None
    return page, next_cursor
