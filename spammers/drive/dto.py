"""Drive v3 resource shapes: file, comment, revision, drive, change, user.

Field names mirror the Drive v3 REST reference exactly — the consumer requests a
``fields`` selector and reads specific keys, so the names are load-bearing. We
return the full documented shape regardless of the ``fields`` mask (extra keys
are harmless to a key-reading consumer).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def iso_ms(dt: Optional[datetime]) -> Optional[str]:
    """RFC3339 with millisecond precision + 'Z' — Drive's timestamp format."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def user_dto(email: Optional[str], name: Optional[str]) -> dict[str, Any]:
    d: dict[str, Any] = {"kind": "drive#user", "displayName": name or "", "me": False}
    if email:
        d["emailAddress"] = email
    return d


def file_dto(f: dict[str, Any]) -> dict[str, Any]:
    """A Drive ``files`` resource row -> the v3 File JSON."""
    out: dict[str, Any] = {
        "kind": "drive#file",
        "id": f["file_id"],
        "name": f["name"],
        "mimeType": f["mime_type"],
        "version": str(f["version"]),
        "trashed": bool(f["trashed"]),
        "explicitlyTrashed": bool(f["explicitly_trashed"]),
        "createdTime": iso_ms(f["created_time"]),
        "modifiedTime": iso_ms(f["modified_time"]),
        "webViewLink": f.get("web_view_link"),
        "owners": [user_dto(f.get("owner_email"), f.get("owner_name"))],
        "lastModifyingUser": user_dto(f.get("last_modifying_email"), f.get("last_modifying_name")),
        "permissions": [
            {"kind": "drive#permission", "type": "user", "role": "owner",
             "emailAddress": f.get("owner_email")},
        ],
        "parents": _jsonb_list(f.get("parents")),
        "shared": bool(f.get("shared")),
        "starred": bool(f.get("starred")),
    }
    if f.get("size") is not None:
        out["size"] = str(f["size"])
    # driveId is set only for Shared Drive items (My Drive files omit it).
    if f.get("drive_kind") == "shared_drive" and f.get("drive_id"):
        out["driveId"] = f["drive_id"]
    return out


def comment_dto(c: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "kind": "drive#comment",
        "id": c["comment_id"],
        "content": c.get("content") or "",
        "createdTime": iso_ms(c["created_time"]),
        "modifiedTime": iso_ms(c["modified_time"]),
        "resolved": bool(c.get("resolved")),
        "author": _comment_author(c.get("author_name"), c.get("author_email")),
        "replies": _jsonb_list(c.get("replies")),
    }
    if c.get("quoted_value"):
        out["quotedFileContent"] = {"mimeType": "text/plain", "value": c["quoted_value"]}
    return out


def revision_dto(r: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "kind": "drive#revision",
        "id": r["revision_id"],
        "modifiedTime": iso_ms(r["modified_time"]),
        "keepForever": bool(r.get("keep_forever")),
        "published": bool(r.get("published")),
        "lastModifyingUser": _comment_author(r.get("last_modifying_name"), r.get("last_modifying_email")),
    }
    if r.get("size") is not None:
        out["size"] = str(r["size"])
    return out


def drive_dto(d: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "drive#drive", "id": d["drive_id"], "name": d["name"]}


def change_dto(f: dict[str, Any]) -> dict[str, Any]:
    """A synthetic Drive change for a file row."""
    removed = bool(f.get("_removed"))
    out: dict[str, Any] = {
        "kind": "drive#change",
        "changeType": "file",
        "time": iso_ms(f["modified_time"]),
        "removed": removed,
        "fileId": f["file_id"],
    }
    if f.get("drive_kind") == "shared_drive" and f.get("drive_id"):
        out["driveId"] = f["drive_id"]
    if not removed:
        out["file"] = file_dto(f)
    return out


def _comment_author(name: Optional[str], email: Optional[str]) -> dict[str, Any]:
    # Drive often omits the author emailAddress on comments for privacy.
    d: dict[str, Any] = {"kind": "drive#user", "displayName": name or "", "me": False}
    if email:
        d["emailAddress"] = email
    return d


def _jsonb_list(v: Any) -> list:
    import json
    if v is None:
        return []
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return []
    return list(v)
