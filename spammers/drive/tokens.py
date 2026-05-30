"""Opaque page / change-feed token codecs for Drive v3.

Drive's ``nextPageToken`` (files.list) and the changes-feed page tokens
(``startPageToken`` / ``newStartPageToken`` / ``nextPageToken``) are opaque
strings. files.list tokens carry an offset; change tokens carry a ``change_seq``
position (the consumer resumes the synthetic changes log strictly at-or-after it).
"""
from __future__ import annotations

import base64
import json
from typing import Optional


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def encode_offset(offset: int) -> str:
    return _b64u(json.dumps({"off": offset}).encode())


def decode_offset(tok: Optional[str]) -> Optional[int]:
    if not tok:
        return None
    try:
        return int(json.loads(_b64u_decode(tok))["off"])
    except Exception:
        return None


def encode_change_token(seq: int) -> str:
    return _b64u(json.dumps({"seq": seq}).encode())


def decode_change_token(tok: Optional[str]) -> Optional[int]:
    if not tok:
        return None
    try:
        return int(json.loads(_b64u_decode(tok))["seq"])
    except Exception:
        return None
