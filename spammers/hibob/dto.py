"""HiBob employee / time-off-change / salary JSON shapes (the REAL api.hibob.com contract).

Pinned against HiBob's official developer docs (apidocs.hibob.com) — the
load-bearing facts the Fyralis Gusto/Brex-archetype clone gets wrong:

  * **People are read via ``POST /v1/people/search``** which returns ALL matching
    employees in one ``{employees:[…]}`` array — there is NO pagination here.
  * **Time-off is the ``/v1/timeoff/requests/changes`` stream** — a BARE ARRAY of
    change snapshots filtered by ``createdOn`` (the change date), windowed by
    ``since``/``to`` — NOT an offset/limit ``/timeoff/requests`` list.
  * **Payroll history is ``/v1/bulk/people/salaries``** — CURSOR-paginated
    (``{results, response_metadata:{next_cursor}}``). Salary money is
    ``base:{value:<number>, currency}`` — a plain NUMBER in major units (the mock
    stores integer cents internally and renders the number).
  * **Timestamp house style is ISO-8601 with MICROSECONDS and NO ``Z``/offset**
    (``creationDateTime``/``creationDate``/``modificationDate``/``triggeredAt`` =
    ``2024-03-27T09:12:21.680867``). ``work.*`` calendar dates render as
    ``DD/MM/YYYY`` strings; time-off + salary calendar dates render ``YYYY-MM-DD``.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional


def iso_noz(dt: Optional[datetime]) -> Optional[str]:
    """HiBob's datetime wire form: ISO-8601, microsecond precision, NO ``Z``/offset."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")


def _ddmmyyyy(d: Optional[date | datetime]) -> Optional[str]:
    """A ``work.*`` calendar date rendered HiBob's human ``DD/MM/YYYY`` way."""
    if d is None:
        return None
    if isinstance(d, datetime):
        d = d.date()
    if isinstance(d, date):
        return d.strftime("%d/%m/%Y")
    return None


def _isodate(d: Optional[date | datetime]) -> Optional[str]:
    """A ``YYYY-MM-DD`` calendar date (time-off / salary fields)."""
    if d is None:
        return None
    if isinstance(d, datetime):
        d = d.date()
    if isinstance(d, date):
        return d.isoformat()
    return None


def money_number(cents: Optional[int]):
    """Render integer cents as HiBob's plain-NUMBER major-unit amount.

    HiBob's salary money is ``{value:<number>, currency}`` — a JSON number in
    major units (NOT cents, NOT a string). Emit an ``int`` when whole, else a
    ``float`` with 2-decimal precision.
    """
    if cents is None:
        return None
    q = (Decimal(int(cents)) / Decimal(100)).quantize(Decimal("0.01"))
    return int(q) if q == q.to_integral_value() else float(q)


def employee_dto(row: dict) -> dict[str, Any]:
    """Project an ``app_hibob.employees`` row into a HiBob employee object."""
    return {
        "id": row["employee_id"],
        "firstName": row["first_name"],
        "surname": row["surname"],
        "secondName": row.get("second_name") or "",
        "fullName": row["full_name"],
        "displayName": row["display_name"],
        "email": row["email"],
        "companyId": row["company_id"],
        "creationDateTime": iso_noz(row["creation_date_time"]),
        "avatarUrl": row.get("avatar_url"),
        "work": {
            "startDate": _ddmmyyyy(row.get("work_start_date")),
            "title": row.get("work_title") or "",
            "department": row.get("work_department") or "",
            "site": row.get("work_site") or "",
            "manager": row.get("work_manager_name"),
            "reportsTo": row.get("work_reports_to_id"),
            "isManager": "Yes" if row.get("work_is_manager") else "No",
            "employeeIdInCompany": row.get("work_employee_id_in_company"),
        },
        "about": {"about": row.get("about_text") or ""},
    }


def timeoff_change_dto(row: dict) -> dict[str, Any]:
    """Project an ``app_hibob.timeoff_changes`` row into a change item."""
    return {
        "requestId": int(row["request_id"]),
        "employeeId": row["employee_id"],
        "employeeDisplayName": row["employee_display_name"],
        "employeeEmail": row["employee_email"],
        "policyTypeDisplayName": row["policy_type_display_name"],
        "changeType": row["change_type"],
        "status": row.get("status") or "approved",
        "createdOn": iso_noz(row["created_on"]),
        "startDate": _isodate(row.get("start_date")),
        "endDate": _isodate(row.get("end_date")),
        "startPortion": "all_day",
        "endPortion": "all_day",
        "durationUnit": row.get("duration_unit") or "days",
        "totalDuration": _num(row.get("total_duration")),
        "totalCost": _num(row.get("total_cost")),
        "type": row.get("request_type") or "days",
    }


def salary_dto(row: dict) -> dict[str, Any]:
    """Project an ``app_hibob.salaries`` row into a bulk-salaries entry."""
    return {
        "id": int(row["salary_id"]),
        "employeeId": row["employee_id"],
        "base": {"value": money_number(row["base_value_cents"]),
                 "currency": row.get("currency") or "USD"},
        "payPeriod": row.get("pay_period") or "Annual",
        "payFrequency": row.get("pay_frequency") or "Monthly",
        "effectiveDate": _isodate(row["effective_date"]),
        "isCurrent": bool(row.get("is_current")),
        "creationDate": iso_noz(row["creation_date"]),
        "modificationDate": iso_noz(row["modification_date"]),
    }


def _num(v):
    """Render a numeric duration/cost as an int when whole, else float."""
    if v is None:
        return 0
    f = float(v)
    return int(f) if f.is_integer() else f


# Wire-level enums (for the seed, tests + the historical/live contract).
CHANGE_TYPES = {"Created", "Canceled", "Deleted", "Pending"}
TIMEOFF_TYPES = {"days", "hours", "portionOnRange", "hoursOnRange", "openEnded"}
PAY_PERIODS = {"Annual", "Hourly", "Daily", "Weekly", "Monthly"}
PAY_FREQUENCIES = {"Monthly", "Semi Monthly", "Weekly", "Bi-Weekly"}
# Webhook event types (Webhooks v2 dotted namespace). The metadata-only ``data``
# envelope + the Bob-Signature scheme are the load-bearing, CONFIRMED parts.
WEBHOOK_EVENT_TYPES = {
    "employee.created", "employee.updated", "employee.deleted", "employee.joined",
    "employee.left", "employee.activated", "employee.inactivated",
    "timeoff.request.requested", "timeoff.request.approved",
    "timeoff.request.declined", "timeoff.request.cancelled",
}
