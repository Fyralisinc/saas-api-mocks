"""GET /calendar/v3/calendars/{calendarId}/events — the three sync modes.

  - full sync:    timeMin/timeMax window, singleEvents, orderBy=startTime
  - incremental:  syncToken (returns only events changed since), showDeleted
  - reconcile:    updatedMin + maxResults=1 probe

An unparseable / "EXPIRED" syncToken returns HTTP 410 (fullSyncRequired), which
drives the consumer back to a full windowed re-sync.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request

from spammers.calendar.auth import resolve_token
from spammers.calendar.dto import event_dto, iso_ms
from spammers.calendar.responses import GoogleJSONResponse as JSONResponse
from spammers.calendar.state import state
from spammers.calendar.tokens import (
    decode_page_token,
    decode_sync_token,
    encode_page_token,
    encode_sync_token,
)
from spammers.common.errors import google_error

router = APIRouter()

_DEFAULT_MAX = 250
_HARD_MAX = 2500


def _parse_rfc3339(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _truthy(s: str | None) -> bool:
    return (s or "").lower() in ("1", "true", "yes")


@router.get("/calendar/v3/calendars/{calendar_id}/events")
async def list_events(request: Request, calendar_id: str):
    claims = resolve_token(request)
    if claims is None:
        return JSONResponse(
            google_error(401, "Invalid Credentials", reason="authError",
                         location="Authorization", location_type="header"),
            status_code=401,
        )

    st = state()
    email = claims.get("sub") if calendar_id == "primary" else calendar_id
    cal = await st.pool.fetchrow(
        """
        SELECT c.id, c.calendar_id, c.summary, c.time_zone
          FROM app_calendar.calendars c
          JOIN app_calendar.accounts a ON a.id = c.account_pk
         WHERE a.run_id = $1 AND c.calendar_id = $2
        """,
        st.run_id, email,
    )
    if cal is None:
        return JSONResponse(google_error(404, "Not Found", reason="notFound"), status_code=404)

    q = request.query_params
    show_deleted = _truthy(q.get("showDeleted"))
    sync_token = q.get("syncToken")
    updated_min = _parse_rfc3339(q.get("updatedMin"))
    try:
        max_results = int(q.get("maxResults", _DEFAULT_MAX))
    except ValueError:
        max_results = _DEFAULT_MAX
    max_results = max(1, min(max_results, _HARD_MAX))

    cal_pk = cal["id"]
    tz = cal["time_zone"] or "UTC"

    # ---- mode selection ----------------------------------------------------
    if sync_token is not None:
        if sync_token == "EXPIRED":
            high_water = None
        else:
            high_water = decode_sync_token(sync_token)
        if high_water is None:
            return JSONResponse(
                google_error(410, "Sync token is no longer valid, a full sync is required.",
                             reason="fullSyncRequired", domain="calendar",
                             location="syncToken", location_type="parameter"),
                status_code=410,
            )
        where = "calendar_pk = $1 AND updated_at > $2"
        params: list = [cal_pk, high_water]
        if not show_deleted:
            where += " AND status <> 'cancelled'"
        order = "updated_at ASC"
        carry_sync = True
    elif updated_min is not None:
        where = "calendar_pk = $1 AND updated_at >= $2"
        params = [cal_pk, updated_min]
        if not show_deleted:
            where += " AND status <> 'cancelled'"
        order = "updated_at ASC"
        carry_sync = True
    else:
        time_min = _parse_rfc3339(q.get("timeMin"))
        time_max = _parse_rfc3339(q.get("timeMax"))
        where = "calendar_pk = $1"
        params = [cal_pk]
        if time_min is not None:
            params.append(time_min)
            where += f" AND end_time > ${len(params)}"
        if time_max is not None:
            params.append(time_max)
            where += f" AND start_time < ${len(params)}"
        if not show_deleted:
            where += " AND status <> 'cancelled'"
        order = "start_time ASC" if q.get("orderBy") == "startTime" else "start_time ASC"
        carry_sync = True

    rows = await st.pool.fetch(
        f"SELECT * FROM app_calendar.events WHERE {where} ORDER BY {order}, event_id ASC",
        *params,
    )

    offset = decode_page_token(q.get("pageToken")) or 0 if q.get("pageToken") else 0
    page = rows[offset:offset + max_results]
    has_more = offset + max_results < len(rows)

    body: dict = {
        "kind": "calendar#events",
        "etag": '"' + str(int(datetime.now(timezone.utc).timestamp() * 1000)) + '"',
        "summary": cal["calendar_id"],
        "updated": iso_ms(datetime.now(timezone.utc)),
        "timeZone": tz,
        "accessRole": "owner",
        "defaultReminders": [],
        "items": [event_dto(dict(r), tz) for r in page],
    }
    if has_more:
        body["nextPageToken"] = encode_page_token(offset + max_results)
    elif carry_sync:
        hw = await st.pool.fetchval(
            "SELECT MAX(updated_at) FROM app_calendar.events WHERE calendar_pk = $1", cal_pk,
        )
        body["nextSyncToken"] = encode_sync_token(hw or datetime.now(timezone.utc))
    return JSONResponse(body)
