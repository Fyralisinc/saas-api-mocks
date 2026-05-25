"""GET /calendar/v3/users/me/calendarList — onboarding calendar enumeration.

Returns the authenticated user's primary calendar (the mock models one calendar
per person, keyed by email).
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from spammers.calendar.auth import resolve_token
from spammers.calendar.dto import calendar_list_entry_dto
from spammers.calendar.responses import GoogleJSONResponse as JSONResponse
from spammers.calendar.state import state
from spammers.common.errors import google_error

router = APIRouter()


@router.get("/calendar/v3/users/me/calendarList")
async def calendar_list(request: Request):
    claims = resolve_token(request)
    if claims is None:
        return JSONResponse(
            google_error(401, "Invalid Credentials", reason="authError",
                         location="Authorization", location_type="header"),
            status_code=401,
        )
    st = state()
    cal = await st.pool.fetchrow(
        """
        SELECT c.calendar_id, c.summary, c.time_zone
          FROM app_calendar.calendars c
          JOIN app_calendar.accounts a ON a.id = c.account_pk
         WHERE a.run_id = $1 AND c.calendar_id = $2
        """,
        st.run_id, claims.get("sub"),
    )
    items = [calendar_list_entry_dto(dict(cal))] if cal else []
    return JSONResponse({
        "kind": "calendar#calendarList",
        "etag": '"0"',
        "items": items,
    })
