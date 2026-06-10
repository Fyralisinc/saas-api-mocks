"""Deel contract + invoice JSON shapes (the REAL ``api.letsdeel.com/rest/v2`` contract).

Pinned against Deel's official developer docs (developer.deel.com) — the load-bearing
facts that the Fyralis Mercury-clone gets wrong:

  * **Money is a decimal STRING in MAJOR units** (``"1000.00"``, ``"220.00"``) — NOT
    integer cents and NOT a JSON number. Each amount has a sibling ``currency``
    (ISO-4217) field. The mock stores integer cents internally and renders the
    2-decimal string on the wire.
  * **List envelopes are ``{data:[…], page:{…}}``** — the rows live under ``data``
    (NOT ``payments``/``contracts``), the pagination metadata under a nested
    ``page`` object (NOT a top-level ``total``).
  * **Contracts paginate CURSOR-only** (``page:{cursor, total_rows}``); **invoices
    HYBRID** (``page:{offset, total_rows, items_per_page, cursor}``).
  * **Timestamps are RFC3339 with milliseconds + Z** (``2022-05-24T09:38:46.235Z``);
    ``start_date`` / ``termination_date`` are DATE-only ``YYYY-MM-DD``.
  * A single contract read is wrapped: ``GET /rest/v2/contracts/{id}`` → ``{data:{…}}``.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional


def _ts(dt: Optional[datetime]) -> Optional[str]:
    """An RFC3339 UTC timestamp with millisecond precision + ``Z`` (Deel's wire form)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _date(d: Optional[date | datetime]) -> Optional[str]:
    """A Deel ``YYYY-MM-DD`` date field (UTC calendar date, no time)."""
    if d is None:
        return None
    if isinstance(d, datetime):
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    return None


def money_str(cents: Optional[int]) -> Optional[str]:
    """Render integer cents as Deel's decimal-STRING major-unit amount (``"1000.00"``)."""
    if cents is None:
        return None
    return str((Decimal(int(cents)) / Decimal(100)).quantize(Decimal("0.01")))


def contract_dto(row: dict) -> dict[str, Any]:
    """Project an ``app_deel.contracts`` row into a Deel Contract object."""
    return {
        "id": row["contract_id"],
        "type": row["type"],
        "title": row["title"],
        "status": row["status"],
        "client": {"legal_entity": {"name": row.get("client_name") or ""}},
        "worker": {
            "full_name": row.get("worker_name") or "",
            "email": row.get("worker_email") or "",
            "country": row.get("worker_country") or "",
        },
        "job_title": row.get("job_title") or "",
        "compensation_details": {
            "amount": money_str(row.get("comp_amount_cents")),
            "currency_code": row.get("comp_currency") or "USD",
            "frequency": row.get("comp_frequency") or "monthly",
            "scale": row.get("comp_scale") or "monthly",
        },
        "external_id": row.get("external_id"),
        "is_archived": bool(row.get("is_archived")),
        "start_date": _date(row.get("start_date")),
        "termination_date": _date(row.get("termination_date")),
        "created_at": _ts(row.get("created_at")),
        "updated_at": _ts(row.get("updated_at")),
    }


def invoice_dto(row: dict) -> dict[str, Any]:
    """Project an ``app_deel.invoices`` row into a Deel Invoice object."""
    return {
        "id": row["invoice_id"],
        "label": row.get("label") or "",
        "total": money_str(row.get("total_cents")),
        "amount": money_str(row.get("amount_cents")),
        "vat_total": money_str(row.get("vat_cents")),
        "deel_fee": money_str(row.get("deel_fee_cents")),
        "status": row["status"],
        "currency": row.get("currency") or "USD",
        "issued_at": _ts(row.get("issued_at")),
        "due_date": _ts(row.get("due_date")),
        "paid_at": _ts(row.get("paid_at")),
        "created_at": _ts(row.get("created_at")),
        "is_overdue": bool(row.get("is_overdue")),
        "contract_id": row["contract_id"],
        "recipient_legal_entity_id": row.get("recipient_legal_entity_id"),
    }


# Wire-level enums (for the seed, tests + the historical/live contract).
CONTRACT_TYPES = {
    "ongoing_time_based", "pay_as_you_go_time_based", "milestones", "eor",
    "employee", "global_payroll", "commissions",
}
CONTRACT_STATUSES = {
    "new", "under_review", "waiting_for_employee_contract", "waiting_for_client_sign",
    "processing_payment", "waiting_for_contractor_sign", "waiting_for_eor_sign",
    "waiting_for_employee_sign", "awaiting_deposit_payment", "in_progress",
    "completed", "cancelled", "user_cancelled", "rejected",
    "waiting_for_client_payment", "onboarding", "waiting_for_approval", "onboarded",
}
INVOICE_STATUSES = {"pending", "paid", "processing", "credited", "refunded"}
# Webhook event types (dotted namespace). The full catalog is at GET /webhooks/events;
# the envelope shape (data.meta.event_type + data.resource[] + timestamp) is the
# load-bearing, CONFIRMED part.
WEBHOOK_EVENT_TYPES = {
    "contract.created", "contract.updated", "contract.status.updated",
    "invoice.created", "invoice.paid", "payment.statement.mark-paid",
}
