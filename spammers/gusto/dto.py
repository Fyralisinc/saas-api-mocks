"""Gusto object JSON shapes (the REAL api.gusto.com ``/v1`` contract).

Pinned against the official Gusto Embedded Payroll reference
(docs.gusto.com/embedded-payroll) + the auto-generated SDK. The load-bearing
facts a connector depends on:

  * **MONEY IS A DECIMAL STRING in MAJOR units (dollars to the cent)** — e.g.
    ``"80000.00"`` / ``"2500.00"`` (the docs: money fields are "string
    representations of numeric decimals … represented to the cent"). NOT cents,
    NOT a number. We store cents internally and project the decimal string.
  * **Datetimes are ISO-8601 with a ``Z`` suffix** (``2025-06-16T16:58:03Z``),
    seconds precision. **Date-only fields** (``check_date``, ``pay_period`` dates,
    ``hire_date``, ``date_of_birth``, ``effective_date``) are ``YYYY-MM-DD``.
  * Employees carry a ``version`` string (optimistic-concurrency / dedup token).
  * The payroll **list** omits ``employee_compensations`` (single-payroll GET
    only) and omits ``totals`` unless ``include=totals`` is requested.

The webhook event ``timestamp`` is the exception — a numeric Unix epoch integer,
not an ISO string (see webhooks.py).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional


def _dt(dt: Optional[datetime]) -> Optional[str]:
    """ISO-8601 with a ``Z`` suffix (Gusto's datetime wire form), seconds precision."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _date(d) -> Optional[str]:
    """A Gusto DATE-only field — ``YYYY-MM-DD``."""
    if d is None:
        return None
    if isinstance(d, datetime):
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    return None


def money(cents: Optional[int]) -> Optional[str]:
    """Project integer cents → a Gusto money STRING in dollars (``"1234.56"``)."""
    if cents is None:
        return None
    sign = "-" if cents < 0 else ""
    c = abs(int(cents))
    return f"{sign}{c // 100}.{c % 100:02d}"


# --------------------------------------------------------------------------- Company


def company_dto(row: dict) -> dict[str, Any]:
    """Project an ``app_gusto.companies`` row into a Gusto Company object."""
    return {
        "uuid": row["company_uuid"],
        "name": row["name"],
        "trade_name": row.get("trade_name"),
        "ein": row.get("ein"),
        "entity_type": row.get("entity_type") or "C-Corporation",
        "company_status": row.get("company_status") or "Approved",
        "is_suspended": False,
        "tier": row.get("tier"),
        "is_partner_managed": True,
        "contractor_only": False,
        "join_date": _date(row.get("join_date")),
        "funding_type": "ach",
        "locations": [],
    }


# -------------------------------------------------------------------------- Employee


def employee_dto(row: dict) -> dict[str, Any]:
    """Project an ``app_gusto.employees`` row into a Gusto Employee object.

    The primary job nests ``compensations[]`` whose ``rate`` is a money STRING.
    """
    rate = money(row.get("rate_cents"))
    job = {
        "uuid": row.get("job_uuid"),
        "version": row["version"],
        "employee_uuid": row["employee_uuid"],
        "title": row.get("job_title"),
        "hire_date": _date(row.get("hire_date")),
        "primary": True,
        "rate": rate,
        "payment_unit": row.get("payment_unit") or "Year",
        "compensations": [
            {
                "uuid": (row.get("job_uuid") or "") + "-comp",
                "version": row["version"],
                "payment_unit": row.get("payment_unit") or "Year",
                "flsa_status": row.get("flsa_status") or "Exempt",
                "rate": rate,
                "effective_date": _date(row.get("hire_date")),
            }
        ],
    }
    return {
        "uuid": row["employee_uuid"],
        "first_name": row.get("first_name") or "",
        "middle_initial": row.get("middle_initial"),
        "last_name": row.get("last_name") or "",
        "email": row.get("email"),
        "work_email": row.get("work_email"),
        "company_uuid": row.get("company_uuid"),
        "manager_uuid": row.get("manager_uuid"),
        "version": row["version"],
        "department": row.get("department"),
        "employee_code": row.get("employee_code"),
        "current_employment_status": row.get("current_employment_status") or "full_time",
        "onboarding_status": row.get("onboarding_status") or "onboarding_completed",
        "terminated": bool(row.get("terminated")),
        "onboarded": bool(row.get("onboarded")),
        "has_ssn": True,
        "ssn": "",
        "date_of_birth": _date(row.get("date_of_birth")),
        "jobs": [job],
        "terminations": ([{"uuid": row["employee_uuid"] + "-term",
                           "effective_date": _date(row.get("termination_date")),
                           "active": False}]
                         if row.get("termination_date") else []),
        "eligible_paid_time_off": [],
        "garnishments": [],
    }


# --------------------------------------------------------------------------- Payroll


def _payroll_totals(row: dict) -> dict[str, Any]:
    """The ``totals`` sub-object — every value a money STRING (dollars)."""
    return {
        "gross_pay": money(row.get("gross_pay_cents")),
        "net_pay": money(row.get("net_pay_cents")),
        "employee_taxes": money(row.get("employee_taxes_cents")),
        "employer_taxes": money(row.get("employer_taxes_cents")),
        "benefits": money(row.get("benefits_cents")),
        "reimbursements": money(row.get("reimbursements_cents")),
        "check_amount": money(row.get("net_pay_cents")),
    }


def payroll_dto(row: dict, *, include_totals: bool = False) -> dict[str, Any]:
    """Project an ``app_gusto.payrolls`` row into a Gusto Payroll (LIST shape).

    The list omits ``employee_compensations`` (single-GET only) and omits
    ``totals`` unless ``include=totals`` was requested.
    """
    out: dict[str, Any] = {
        "uuid": row["payroll_uuid"],
        "payroll_uuid": row["payroll_uuid"],
        "company_uuid": row.get("company_uuid"),
        "pay_period": {
            "start_date": _date(row.get("pay_period_start")),
            "end_date": _date(row.get("pay_period_end")),
            "pay_schedule_uuid": row.get("pay_schedule_uuid"),
        },
        "check_date": _date(row.get("check_date")),
        "processed": bool(row.get("processed")),
        "processed_date": _date(row.get("processed_at")),
        "calculated_at": _dt(row.get("calculated_at")),
        "payroll_deadline": _dt(row.get("payroll_deadline")),
        "off_cycle": bool(row.get("off_cycle")),
        "external": bool(row.get("external")),
        "auto_payroll": False,
        "payroll_type": row.get("payroll_type") or "regular",
        "created_at": _dt(row.get("calculated_at")),
    }
    if include_totals:
        out["totals"] = _payroll_totals(row)
    return out


def payroll_detail_dto(row: dict, comps: list[dict]) -> dict[str, Any]:
    """The single-payroll GET shape: the list shape + ``totals`` +
    ``employee_compensations`` (per-employee gross/net, money STRINGS)."""
    out = payroll_dto(row, include_totals=True)
    out["employee_compensations"] = comps
    return out


# Wire-level enums (for the seed, tests + the historical/live contract).
EMPLOYMENT_STATUSES = {
    "full_time", "part_time", "variable", "seasonal", "temporary",
    "part_time_eligible", "full_time_temporary",
}
ONBOARDING_STATUSES = {
    "onboarding_completed", "admin_onboarding_incomplete",
    "self_onboarding_pending_invite", "self_onboarding_invited",
}
PROCESSING_STATUSES = {"processed", "unprocessed"}
PAYROLL_TYPES = {"regular", "off_cycle", "external"}
PAYMENT_UNITS = {"Year", "Month", "Week", "Hour", "Paycheck"}
# Webhook event types relevant to these streams (the full catalog is larger).
WEBHOOK_EVENT_TYPES = {
    "payroll.created", "payroll.calculated", "payroll.submitted",
    "payroll.processed", "payroll.paid", "payroll.cancelled",
    "employee.created", "employee.updated", "employee.onboarded",
    "employee.terminated", "employee.rehired",
}
