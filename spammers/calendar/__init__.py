"""Google Calendar mock (:7005).

Wire-compatible with the Calendar API v3 surface the consumer's poller uses:
DWD ``POST /token``, ``GET /calendar/v3/calendars/{calendarId}/events`` in its
three modes (full sync with ``timeMin``/``singleEvents``/``orderBy``, incremental
``syncToken``, and the ``updatedMin`` reconcile probe), and
``GET /calendar/v3/users/me/calendarList``. Serves the latest run resolved at
startup; nothing here is imported by the other mocks.
"""
