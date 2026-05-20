"""Pagination helpers — Slack cursor, GitHub Link header, Gmail nextPageToken,
Discord before/after on snowflake-sorted lists.

The cursor formats here mirror what each real provider emits so that
Fyralis-side parsers don't care whether the response came from the mock or
the real API.
"""
from __future__ import annotations

import base64
import json
from typing import Any, Iterable, Optional, Tuple


# ---------- Generic cursor ----------

def encode_cursor(state: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(state, sort_keys=True).encode("utf-8")).decode("ascii")


def decode_cursor(cursor: Optional[str]) -> dict[str, Any]:
    if not cursor:
        return {}
    try:
        return json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
    except Exception:
        return {}


# ---------- Slack ----------

def slack_paginate(
    items: list[Any],
    *,
    cursor: Optional[str],
    limit: int,
) -> Tuple[list[Any], dict[str, Any]]:
    """Slice ``items`` per cursor. Returns ``(page, response_metadata)``."""
    state = decode_cursor(cursor)
    offset = int(state.get("o", 0))
    page = items[offset: offset + limit]
    next_offset = offset + limit
    meta: dict[str, Any] = {}
    if next_offset < len(items):
        meta["response_metadata"] = {"next_cursor": encode_cursor({"o": next_offset})}
    return page, meta


# ---------- GitHub Link header ----------

def github_link_header(
    *, base_url: str, path: str, per_page: int, page: int, total_pages: int,
) -> Optional[str]:
    """Build the ``Link:`` value GitHub returns on paginated responses.

    e.g. ``<https://api.github.com/installation/repositories?per_page=30&page=2>; rel="next",
           <https://api.github.com/installation/repositories?per_page=30&page=5>; rel="last"``
    """
    if total_pages <= 1:
        return None
    rels: list[str] = []
    if page < total_pages:
        rels.append(f'<{base_url}{path}?per_page={per_page}&page={page + 1}>; rel="next"')
    if page > 1:
        rels.append(f'<{base_url}{path}?per_page={per_page}&page={page - 1}>; rel="prev"')
    rels.append(f'<{base_url}{path}?per_page={per_page}&page={total_pages}>; rel="last"')
    rels.append(f'<{base_url}{path}?per_page={per_page}&page=1>; rel="first"')
    return ", ".join(rels)


# ---------- Gmail nextPageToken ----------

def gmail_page_token(state: dict[str, Any]) -> str:
    return encode_cursor(state)


def gmail_decode_token(token: Optional[str]) -> dict[str, Any]:
    return decode_cursor(token)


# ---------- Discord before/after on snowflake ----------

def discord_filter_by_snowflake(
    items: list[dict[str, Any]],
    *,
    before: Optional[str] = None,
    after: Optional[str] = None,
    around: Optional[str] = None,
    limit: int = 50,
    id_field: str = "id",
) -> list[dict[str, Any]]:
    """Discord returns messages newest-first. ``before`` = older than the
    given snowflake; ``after`` = newer than. Snowflakes are sortable as
    fixed-width integer strings.
    """
    def as_int(s: Optional[str]) -> Optional[int]:
        try:
            return int(s) if s is not None else None
        except (TypeError, ValueError):
            return None

    b = as_int(before)
    a = as_int(after)
    ar = as_int(around)
    out: Iterable[dict[str, Any]] = items
    if b is not None:
        out = [x for x in out if int(x[id_field]) < b]
    if a is not None:
        out = [x for x in out if int(x[id_field]) > a]
    if ar is not None:
        out = sorted(out, key=lambda x: abs(int(x[id_field]) - ar))[:limit]
        return out
    out = sorted(out, key=lambda x: int(x[id_field]), reverse=True)
    return list(out)[:limit]
