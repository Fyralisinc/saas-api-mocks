"""Figma version / comment / user / file JSON shapes (the REAL api.figma.com contract).

Pinned against Figma's official developer docs (developers.figma.com) AND the
canonical OpenAPI spec (github.com/figma/rest-api-spec) — the load-bearing facts
the Fyralis Brex-Bearer-archetype clone gets wrong:

  * A real backfill has NO single ``/v1/files/{key}/events`` endpoint. It MERGES
    ``GET /v1/files/{key}/versions`` (``{versions:[…], pagination:{prev_page,
    next_page}}``, CURSOR ``page_size``/``before``/``after``) with
    ``GET /v1/files/{key}/comments`` (``{comments:[…]}`` — NO pagination) into one
    event stream, after enumerating files via teams → projects → files.
  * The **User** object is ``{id, handle, img_url}`` — a numeric-string ``id`` and
    **NO ``email``** (email is present only on ``GET /v1/me``).
  * **Timestamps are UTC ISO-8601 with ``Z``** (``2021-01-01T00:00:00Z``).
  * A version's ``label``/``description`` are ``null`` for an auto-save.
  * A comment's ``order_id`` is ``string | null`` (the OpenAPI spec — NOT a Number,
    despite the prose docs), ``message`` is a required string, and ``client_meta``
    is a Vector ``{x,y}`` or FrameOffset ``{node_id, node_offset:{x,y}}``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional


def iso_z(dt: Optional[datetime]) -> Optional[str]:
    """Figma's datetime wire form: UTC ISO-8601, second precision, trailing ``Z``."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def user_dto(row: dict) -> dict[str, Any]:
    """A Figma User object: ``{id, handle, img_url}`` — NO email (that is /v1/me only)."""
    return {
        "id": row["figma_user_id"],
        "handle": row["handle"],
        "img_url": row["img_url"],
    }


def me_dto(row: dict) -> dict[str, Any]:
    """The ``GET /v1/me`` shape — the ONE place the User carries ``email``."""
    return {
        "id": row["figma_user_id"],
        "email": row.get("email") or "",
        "handle": row["handle"],
        "img_url": row["img_url"],
    }


def version_dto(row: dict, user: dict) -> dict[str, Any]:
    """Project an ``app_figma.versions`` row into a Figma Version object.

    ``label``/``description`` are ``null`` for an auto-save (emitted as JSON null,
    NOT dropped — the OpenAPI spec lists them as required-present, nullable).
    """
    return {
        "id": row["version_id"],
        "created_at": iso_z(row["created_at"]),
        "label": row.get("label"),
        "description": row.get("description"),
        "user": user_dto(user),
        "thumbnail_url": row.get("thumbnail_url")
        or f"https://figma-alpha-api.s3.us-west-2.amazonaws.com/thumb/{row['version_id']}",
    }


def _as_json(val: Any, default: Any) -> Any:
    """asyncpg returns jsonb columns as a str (no codec registered) — decode it."""
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (ValueError, TypeError):
            return default
    return val if val is not None else default


def comment_dto(row: dict, user: dict) -> dict[str, Any]:
    """Project an ``app_figma.comments`` row into a Figma Comment object."""
    cm = _as_json(row.get("client_meta"), {})
    if not isinstance(cm, dict):
        cm = {}
    reactions = _as_json(row.get("reactions"), [])
    if not isinstance(reactions, list):
        reactions = []
    return {
        "id": row["comment_id"],
        "file_key": row["file_key"],
        "parent_id": row.get("parent_id") or "",
        "user": user_dto(user),
        "created_at": iso_z(row["created_at"]),
        "resolved_at": iso_z(row.get("resolved_at")),  # null when unresolved
        "message": row["message"],
        "client_meta": cm,
        "order_id": row.get("order_id"),               # string | null (spec, not Number)
        "reactions": reactions,
    }


def project_dto(row: dict) -> dict[str, Any]:
    """A project entry under ``GET /v1/teams/{id}/projects``: ``{id, name}``."""
    return {"id": row["project_id"], "name": row["name"]}


def file_listing_dto(row: dict) -> dict[str, Any]:
    """A file entry under ``GET /v1/projects/{id}/files``."""
    return {
        "key": row["file_key"],
        "name": row["name"],
        "thumbnail_url": row["thumbnail_url"],
        "last_modified": iso_z(row["last_modified"]),
    }


def file_meta_dto(row: dict, creator: Optional[dict], last_touched_by: Optional[dict]) -> dict[str, Any]:
    """The ``GET /v1/files/{key}/meta`` shape — wrapped in ``{file:{…}}`` by the app.

    Note the snake_case keys here (``last_touched_at``/``thumbnail_url``) vs the full
    ``GET /v1/files/{key}`` endpoint's camelCase (``lastModified``/``thumbnailUrl``)."""
    meta: dict[str, Any] = {
        "name": row["name"],
        "folder_name": row["folder_name"],
        "last_touched_at": iso_z(row["last_modified"]),
        "thumbnail_url": row["thumbnail_url"],
        "editorType": row.get("editor_type") or "figma",
        "role": "owner",
        "link_access": "org_view",
        "url": f"https://www.figma.com/design/{row['file_key']}/{row['name'].replace(' ', '-')}",
        "version": row.get("current_version_id") or "",
    }
    if creator is not None:
        meta["creator"] = user_dto(creator)
    if last_touched_by is not None:
        meta["last_touched_by"] = user_dto(last_touched_by)
    return meta


# Wire-level enums (for the seed, tests + the historical/live contract).
WEBHOOK_EVENT_TYPES = {
    "PING", "FILE_UPDATE", "FILE_VERSION_UPDATE", "FILE_DELETE", "FILE_COMMENT",
    "LIBRARY_PUBLISH", "DEV_MODE_STATUS_UPDATE",
}
EDITOR_TYPES = {"figma", "figjam", "slides", "buzz", "sites", "make"}
