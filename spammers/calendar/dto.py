"""Event / calendarList response shapes, matching Calendar API v3."""
from __future__ import annotations

import json
from datetime import datetime, timezone


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_ms(dt: datetime) -> str:
    """RFC3339 with milliseconds + Z, as Calendar uses for created/updated."""
    return _utc(dt).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def iso_z(dt: datetime) -> str:
    """RFC3339 second precision + Z, as Calendar uses for start/end dateTime."""
    return _utc(dt).strftime("%Y-%m-%dT%H:%M:%SZ")


def _etag(row: dict) -> str:
    return '"' + str(int(_utc(row["updated_at"]).timestamp() * 1000)) + '"'


def event_dto(row: dict, calendar_tz: str = "UTC") -> dict:
    """Full Event resource. Cancelled events return Calendar's minimal shape."""
    if row["status"] == "cancelled":
        out = {
            "kind": "calendar#event",
            "etag": _etag(row),
            "id": row["event_id"],
            "status": "cancelled",
        }
        if row.get("recurring_event_id"):
            out["recurringEventId"] = row["recurring_event_id"]
        return out

    out: dict = {
        "kind": "calendar#event",
        "etag": _etag(row),
        "id": row["event_id"],
        "status": row["status"],
        "htmlLink": row.get("html_link") or "",
        "created": iso_ms(row["created_at"]),
        "updated": iso_ms(row["updated_at"]),
        "summary": row.get("summary") or "",
    }
    if row.get("description"):
        out["description"] = row["description"]
    if row.get("location"):
        out["location"] = row["location"]
    if row.get("creator_email"):
        out["creator"] = {"email": row["creator_email"]}
    if row.get("organizer_email"):
        out["organizer"] = {"email": row["organizer_email"], "self": True}

    if row.get("all_day"):
        out["start"] = {"date": _utc(row["start_time"]).strftime("%Y-%m-%d")}
        out["end"] = {"date": _utc(row["end_time"]).strftime("%Y-%m-%d")}
    else:
        out["start"] = {"dateTime": iso_z(row["start_time"]), "timeZone": calendar_tz}
        out["end"] = {"dateTime": iso_z(row["end_time"]), "timeZone": calendar_tz}

    if row.get("recurring_event_id"):
        out["recurringEventId"] = row["recurring_event_id"]
    out["iCalUID"] = row["ical_uid"]
    out["sequence"] = row.get("sequence", 0)

    attendees = row.get("attendees")
    if isinstance(attendees, str):
        attendees = json.loads(attendees)
    if attendees:
        out["attendees"] = attendees
    if row.get("hangout_link"):
        out["hangoutLink"] = row["hangout_link"]
    out["eventType"] = row.get("event_type") or "default"
    out["reminders"] = {"useDefault": True}
    return out


def calendar_list_entry_dto(cal: dict) -> dict:
    return {
        "kind": "calendar#calendarListEntry",
        "etag": '"' + str(abs(hash(cal["calendar_id"])) % 10**12) + '"',
        "id": cal["calendar_id"],
        "summary": cal.get("summary") or cal["calendar_id"],
        "timeZone": cal.get("time_zone") or "UTC",
        "colorId": "1",
        "accessRole": "owner",
        "primary": True,
        "selected": True,
    }
