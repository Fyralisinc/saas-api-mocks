"""Miro board / item JSON shapes (the REAL api.miro.com ``/v2`` contract).

Pinned against Miro's published OpenAPI spec (the source Miro generates all its
official SDK clients from). The load-bearing facts the Fyralis Brex-Bearer-archetype
clone gets wrong (it assumes "everything is the same paginator"):

  * ``GET /v2/boards`` is OFFSET-paginated (``{data,total,size,offset,limit,links,
    type}``) while ``GET /v2/boards/{id}/items`` is CURSOR-paginated
    (``{data,total,size,cursor,limit,links}`` — NO top-level ``type``; ``cursor`` is
    ABSENT on the final page). Two different paginators.
  * The **board** user objects (``owner``/``createdBy``/``modifiedBy``) are
    ``{id, name, type:"user"}`` — they carry ``name``. The **item** user objects
    (``createdBy``/``modifiedBy``) are ``{id, type:"user"}`` — NO ``name``.
  * Items have NO version field: only ``createdAt``/``modifiedAt`` ISO-8601 with
    **millisecond** precision and a trailing ``Z`` (``2022-03-30T17:26:50.000Z``).
  * ``geometry`` = ``{width, height, rotation}``; ``position`` = ``{x, y, origin,
    relativeTo}``; ``parent`` = ``{id}``; ``data`` is the type-specific
    WidgetDataOutput (sticky_note/shape/text/card/frame …) emitted verbatim.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, Optional


def iso_ms(dt: Optional[datetime]) -> Optional[str]:
    """Miro's datetime wire form: UTC ISO-8601, **millisecond** precision, ``Z``."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _as_json(val: Any, default: Any) -> Any:
    """asyncpg returns jsonb columns as a str (no codec registered) — decode it."""
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (ValueError, TypeError):
            return default
    return val if val is not None else default


# --------------------------------------------------------------------------- users

def board_user_dto(row: Optional[dict]) -> Optional[dict[str, Any]]:
    """A board-scoped user object: ``{id, name, type:"user"}`` (carries ``name``)."""
    if row is None:
        return None
    return {"id": row["miro_user_id"], "name": row["name"], "type": "user"}


def item_user_dto(row: Optional[dict]) -> Optional[dict[str, Any]]:
    """An item-scoped user object: ``{id, type:"user"}`` — NO ``name``."""
    if row is None:
        return None
    return {"id": row["miro_user_id"], "type": "user"}


def board_member_dto(row: Optional[dict]) -> Optional[dict[str, Any]]:
    """The ``currentUserMembership`` BoardMember: ``{id, name, role, type:"board_member"}``."""
    if row is None:
        return None
    return {"id": row["miro_user_id"], "name": row["name"],
            "role": row.get("role") or "editor", "type": "board_member"}


# --------------------------------------------------------------------------- boards

def board_dto(row: dict, *, owner: Optional[dict], created_by: Optional[dict],
              modified_by: Optional[dict], member: Optional[dict],
              team_id: str, team_name: str, base_url: str,
              with_links: bool = False) -> dict[str, Any]:
    """Project an ``app_miro.boards`` row into a Miro Board object.

    Required wire fields: ``id``, ``name``, ``description``, ``type``. The single-board
    ``GET /v2/boards/{id}`` adds a ``links`` object (``{self, related}``); the list
    response omits it.
    """
    out: dict[str, Any] = {
        "id": row["board_id"],
        "name": row["name"],
        "description": row.get("description") or "",
        "type": "board",
        "viewLink": row["view_link"],
        "policy": {
            "permissionsPolicy": {
                "collaborationToolsStartAccess": "all_editors",
                "copyAccess": "anyone",
                "sharingAccess": "team_members_with_editing_rights",
            },
            "sharingPolicy": {
                "access": "private",
                "inviteToAccountAndBoardLinkAccess": "no_access",
                "organizationAccess": "view",
                "teamAccess": "edit",
            },
        },
        "team": {"id": team_id, "name": team_name, "type": "team"},
        "createdAt": iso_ms(row["created_at"]),
        "modifiedAt": iso_ms(row["modified_at"]),
    }
    o = board_user_dto(owner)
    if o is not None:
        out["owner"] = o
    cb = board_user_dto(created_by)
    if cb is not None:
        out["createdBy"] = cb
    mb = board_user_dto(modified_by)
    if mb is not None:
        out["modifiedBy"] = mb
    m = board_member_dto(member)
    if m is not None:
        out["currentUserMembership"] = m
    if row.get("last_opened_at") is not None:
        out["lastOpenedAt"] = iso_ms(row["last_opened_at"])
        if m is not None:
            out["lastOpenedBy"] = {"id": member["miro_user_id"],
                                   "name": member["name"], "type": "user"}
    if with_links:
        bid = row["board_id"]
        out["links"] = {
            "self": f"{base_url}/boards/{bid}",
            "related": f"{base_url}/boards/{bid}/members?limit=20&offset=0",
        }
    return out


# --------------------------------------------------------------------------- items

def item_dto(row: dict, *, created_by: Optional[dict],
             modified_by: Optional[dict]) -> dict[str, Any]:
    """Project an ``app_miro.items`` row into a Miro GenericItem.

    Required wire fields: ``id``, ``type``. ``data``/``geometry``/``position`` are the
    type-specific sub-objects; ``createdBy``/``modifiedBy`` carry NO ``name``; there is
    NO version field (only ``createdAt``/``modifiedAt``).
    """
    out: dict[str, Any] = {
        "id": row["item_id"],
        "type": row["item_type"],
        "data": _as_json(row.get("data"), {}),
        "createdAt": iso_ms(row["created_at"]),
        "modifiedAt": iso_ms(row["modified_at"]),
    }
    geom = _as_json(row.get("geometry"), None)
    if isinstance(geom, dict):
        out["geometry"] = geom
    pos = _as_json(row.get("position"), None)
    if isinstance(pos, dict):
        out["position"] = pos
    if row.get("parent_id"):
        out["parent"] = {"id": row["parent_id"]}
    cb = item_user_dto(created_by)
    if cb is not None:
        out["createdBy"] = cb
    mb = item_user_dto(modified_by)
    if mb is not None:
        out["modifiedBy"] = mb
    return out


# --------------------------------------------------------------------------- cursor

def encode_cursor(seq: int) -> str:
    """Opaque item cursor — base64 of the last returned ``item_seq`` (round-tripped
    verbatim by the consumer; it never parses it)."""
    return base64.urlsafe_b64encode(f"after:{seq}".encode()).decode().rstrip("=")


def decode_cursor(cursor: Optional[str]) -> Optional[int]:
    """Decode an opaque item cursor back to its ``item_seq`` floor, or None if absent/bad."""
    if not cursor:
        return None
    try:
        pad = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + pad).decode()
        if raw.startswith("after:"):
            return int(raw[len("after:"):])
    except (ValueError, TypeError):
        return None
    return None


# Wire-level enums (for the seed + tests + the historical contract).
ITEM_TYPES = {
    "text", "shape", "sticky_note", "image", "document", "card", "app_card",
    "frame", "embed",
}
SHAPE_KINDS = {
    "rectangle", "round_rectangle", "circle", "triangle", "rhombus",
    "parallelogram", "trapezoid", "pentagon", "hexagon", "octagon", "star",
    "cloud", "cross", "can", "right_arrow", "left_arrow",
}
STICKY_SHAPES = {"square", "rectangle"}
FRAME_FORMATS = {"custom", "desktop", "phone", "tablet", "a4", "letter",
                 "ratio_1x1", "ratio_4x3", "ratio_16x9"}
FRAME_TYPES = {"freeform", "heap", "grid", "rows", "columns"}
BOARD_ROLES = {"viewer", "commenter", "editor", "coowner", "owner"}
