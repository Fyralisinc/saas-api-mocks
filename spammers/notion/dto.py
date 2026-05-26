"""Notion object shapes (API version 2022-06-28)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _jsonb(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


def partial_user(user_id: str | None) -> dict:
    return {"object": "user", "id": user_id}


def _emoji_icon(icon: str | None) -> dict | None:
    return {"type": "emoji", "emoji": icon} if icon else None


def _rich_text(content: str) -> list:
    return [{
        "type": "text",
        "text": {"content": content, "link": None},
        "annotations": {"bold": False, "italic": False, "strikethrough": False,
                        "underline": False, "code": False, "color": "default"},
        "plain_text": content,
        "href": None,
    }]


def _parent(parent_type: str, parent_id: str | None) -> dict:
    if parent_type == "database_id":
        return {"type": "database_id", "database_id": parent_id}
    if parent_type == "page_id":
        return {"type": "page_id", "page_id": parent_id}
    return {"type": "workspace", "workspace": True}


def page_dto(row: dict) -> dict:
    return {
        "object": "page",
        "id": row["page_id"],
        "created_time": iso(row["created_time"]),
        "last_edited_time": iso(row["last_edited_time"]),
        "created_by": partial_user(row.get("created_by")),
        "last_edited_by": partial_user(row.get("created_by")),
        "cover": None,
        "icon": _emoji_icon(row.get("icon")),
        "parent": _parent(row["parent_type"], row.get("parent_id")),
        "archived": row.get("archived", False),
        "in_trash": row.get("archived", False),
        "properties": _jsonb(row.get("properties") or {}),
        "url": row["url"],
        "public_url": None,
    }


def database_dto(row: dict) -> dict:
    return {
        "object": "database",
        "id": row["database_id"],
        "created_time": iso(row["created_time"]),
        "last_edited_time": iso(row["last_edited_time"]),
        "created_by": partial_user(None),
        "last_edited_by": partial_user(None),
        "title": _rich_text(row["title"]),
        "description": [],
        "icon": _emoji_icon(row.get("icon")),
        "cover": None,
        "properties": _jsonb(row.get("properties_schema") or {}),
        "parent": _parent(row.get("parent_type", "workspace"), row.get("parent_id")),
        "url": row["url"],
        "archived": False,
        "in_trash": False,
        "is_inline": False,
        "public_url": None,
    }


def block_dto(row: dict, page_id: str) -> dict:
    return {
        "object": "block",
        "id": row["block_id"],
        "parent": {"type": "page_id", "page_id": page_id},
        "created_time": iso(row["created_time"]),
        "last_edited_time": iso(row["last_edited_time"]),
        "created_by": partial_user(row.get("created_by")),
        "last_edited_by": partial_user(row.get("created_by")),
        "has_children": row.get("has_children", False),
        "archived": False,
        "in_trash": False,
        "type": row["type"],
        row["type"]: _jsonb(row.get("content") or {}),
    }


def comment_dto(row: dict) -> dict:
    return {
        "object": "comment",
        "id": row["comment_id"],
        "parent": {"type": "page_id", "page_id": row["parent_page_id"]},
        "discussion_id": row["discussion_id"],
        "created_time": iso(row["created_time"]),
        "last_edited_time": iso(row["last_edited_time"]),
        "created_by": partial_user(row.get("created_by")),
        "rich_text": _jsonb(row.get("rich_text") or []),
    }


def person_user_dto(person_id, name: str | None, email: str | None) -> dict:
    return {
        "object": "user",
        "id": str(person_id),
        "name": name,
        "avatar_url": None,
        "type": "person",
        "person": ({"email": email} if email else {}),
    }


def bot_user_dto(st) -> dict:
    return {
        "object": "user",
        "id": st.bot_user_id,
        "name": st.bot_name,
        "avatar_url": None,
        "type": "bot",
        "bot": {
            "owner": {"type": "workspace", "workspace": True},
            "workspace_name": st.workspace_name,
        },
    }


def list_dto(results: list, *, next_cursor: str | None, type_key: str) -> dict:
    return {
        "object": "list",
        "results": results,
        "next_cursor": next_cursor,
        "has_more": next_cursor is not None,
        "type": type_key,
        type_key: {},
    }
