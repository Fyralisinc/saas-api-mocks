"""Fireflies object JSON shapes (the REAL api.fireflies.ai GraphQL contract).

Pinned against docs.fireflies.ai (the GraphQL schema + Transcript/User type pages).
The load-bearing facts:

  * A Transcript's ``date`` is a **Float = epoch MILLISECONDS** (creation, UTC) —
    e.g. ``1713816844454`` — NOT an ISO string and NOT seconds. ``dateString`` is
    the SEPARATE ISO-8601 ``…Z`` string with millisecond precision
    (``"2024-04-22T20:14:04.454Z"``). ``duration`` is a ``Number`` in **MINUTES**
    (not seconds).
  * There is **NO** ``updatedAt`` / ``processedAt`` / ``version`` field on a
    Transcript — the dedup "content version" the connector wants is DERIVED (the
    ``date`` epoch-ms, or the ``meeting_info.summary_status`` transition). The mock
    keeps an internal ``version`` column but never emits it on the wire.
  * ``participants`` / ``fireflies_users`` are ``[String]`` (emails);
    ``meeting_attendees`` are objects ``{displayName,email,phoneNumber,name,
    location}``; ``speakers`` are ``{id,name}``.
  * The ``user`` query (no id) returns the API-KEY OWNER — Fireflies' real
    "verify my token". There is no first-class "workspace id"; identity is the key
    owner's ``user_id``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional


def _j(v: Any) -> Any:
    """Decode a JSONB column (asyncpg can hand these back as a ``str``)."""
    if v is None:
        return None
    if isinstance(v, (str, bytes, bytearray)):
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return v
    return v


def _epoch_ms(dt: Optional[datetime]) -> Optional[int]:
    """A Fireflies ``date`` — epoch MILLISECONDS (a JSON number)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


def _date_string(dt: Optional[datetime]) -> Optional[str]:
    """A Fireflies ``dateString`` — ISO-8601 with milliseconds + ``Z``."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def user_dto(ws: dict, *, num_transcripts: int = 0) -> dict[str, Any]:
    """Project the workspace's owner identity into a Fireflies ``User``.

    This is what ``user`` (no id) returns — the API-key owner (the real
    equivalent of the Fyralis-cloned fake ``GET /workspace``)."""
    return {
        "user_id": ws["owner_user_id"],
        "name": ws.get("owner_name") or "",
        "email": ws.get("owner_email") or "",
        "num_transcripts": int(num_transcripts),
        "minutes_consumed": int(num_transcripts) * 47,
        "is_admin": True,
        "integrations": ["google_calendar", "slack", "zoom"],
        "user_groups": [],
    }


def transcript_dto(row: dict) -> dict[str, Any]:
    """Project an ``app_fireflies.transcripts`` row into a Fireflies ``Transcript``."""
    dt = row.get("meeting_date")
    attendees = _j(row.get("meeting_attendees")) or []
    participants = _j(row.get("participants")) or []
    return {
        "id": row["transcript_id"],
        "title": row.get("title") or "",
        # epoch-MILLISECONDS Float (creation) + the separate ISO-Z string.
        "date": _epoch_ms(dt),
        "dateString": _date_string(dt),
        "duration": float(row.get("duration_minutes") or 0),  # MINUTES
        "transcript_url": row.get("transcript_url"),
        "audio_url": row.get("audio_url"),
        "video_url": row.get("video_url"),
        "meeting_link": row.get("meeting_link"),
        "host_email": row.get("host_email"),
        "organizer_email": row.get("organizer_email"),
        "participants": list(participants),
        "fireflies_users": _j(row.get("fireflies_users")) or [],
        "meeting_attendees": list(attendees),
        "speakers": _j(row.get("speakers")) or [],
        "sentences": _j(row.get("sentences")) or [],
        "summary": _j(row.get("summary")),
        "meeting_info": _j(row.get("meeting_info")) or {
            "fred_joined": True, "silent_meeting": False, "summary_status": "processed",
        },
        "calendar_id": row.get("calendar_id"),
        "cal_id": row.get("calendar_id"),
        "client_reference_id": row.get("client_reference_id"),
        "user": None,  # populated only when the workspace owner is requested/joined
    }


# The documented Fireflies error codes (extensions.code) and their HTTP statuses.
# (docs.fireflies.ai/miscellaneous/error-codes + .../fundamentals/authorization)
ERROR_HTTP_STATUS = {
    "auth_failed": 401,
    "forbidden": 403,
    "not_in_team": 403,
    "require_elevated_privilege": 403,
    "paid_required": 403,
    "account_cancelled": 403,
    "object_not_found": 404,
    "invalid_arguments": 400,
    "args_required": 400,
    "payload_too_small": 400,
    "invalid_language_code": 400,
    "request_timeout": 408,
    "require_ai_credits": 402,
    "too_many_requests": 429,
    "invariant_violation": 500,
}

# Webhook V2 event names (docs.fireflies.ai/graphql-api/webhooks-v2).
WEBHOOK_EVENTS = {"meeting.transcribed", "meeting.summarized"}
