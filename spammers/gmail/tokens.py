"""Opaque offset-based page tokens for Gmail list endpoints."""
from __future__ import annotations

import base64
import json
from typing import Optional


def encode_page_token(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"o": offset}).encode()).decode().rstrip("=")


def decode_page_token(tok: Optional[str]) -> int:
    if not tok:
        return 0
    try:
        raw = base64.urlsafe_b64decode(tok + "=" * (-len(tok) % 4))
        return int(json.loads(raw)["o"])
    except Exception:
        return 0
