"""Ramp object JSON shapes (the REAL api.ramp.com ``/developer/v1`` contract).

Pinned against Ramp's official OpenAPI (docs.ramp.com/openapi/developer-api.json)
+ the published guides. The load-bearing facts:

  * **MONEY IS DUAL.** The top-level ``amount`` on a Transaction/Reimbursement is a
    **NUMBER in MAJOR units (dollars)** — e.g. ``90.0`` (a legacy representation,
    still on the field). The canonical/nested ``CurrencyAmount`` fields
    (``original_transaction_amount``, ``line_items[].amount``,
    ``original_reimbursement_amount``) are ``{amount:<int CENTS>, currency_code,
    minor_unit_conversion_rate}``. We store cents and project BOTH.
  * **Transactions key ``currency_code``; reimbursements key ``currency``** (the API
    is deliberately inconsistent between the two resources).
  * **Timestamps are ISO-8601 with a ``+00:00`` OFFSET** (NOT a ``Z`` suffix), no
    microseconds — e.g. ``2022-05-03T00:00:00+00:00``. Reimbursement
    ``transaction_date`` is **DATE-only** ``YYYY-MM-DD``.
  * Transaction ``state`` ∈ the 7-value enum; ``sync_status`` ∈ the 3-value enum.
    The card-holder nests as an object ``card_holder{user_id, first_name, …}``.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional


def _dt(dt: Optional[datetime]) -> Optional[str]:
    """ISO-8601 with a ``+00:00`` offset (Ramp's wire form), seconds precision."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _date(dt) -> Optional[str]:
    """A Ramp DATE-only field — ``YYYY-MM-DD`` (UTC calendar date, no time)."""
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).date().isoformat()
    if isinstance(dt, date):
        return dt.isoformat()
    return None


def _dollars(cents: Optional[int]) -> Optional[float]:
    """The top-level legacy ``amount`` — a NUMBER in MAJOR units (dollars)."""
    if cents is None:
        return None
    return round(cents / 100, 2)


def currency_amount(cents: Optional[int], currency_code: str = "USD") -> Optional[dict[str, Any]]:
    """A Ramp canonical ``CurrencyAmount`` — ``{amount:<int CENTS>, currency_code,
    minor_unit_conversion_rate}`` (the nested/preferred money representation)."""
    if cents is None:
        return None
    return {
        "amount": int(cents),
        "currency_code": currency_code or "USD",
        "minor_unit_conversion_rate": 100,
    }


def transaction_dto(row: dict) -> dict[str, Any]:
    """Project an ``app_ramp.transactions`` row into a Ramp Transaction."""
    cents = int(row["amount_cents"])
    ccode = row.get("currency_code") or "USD"
    line_item = {
        "amount": currency_amount(cents, ccode),
        "converted_amount": currency_amount(cents, ccode),
        "memo": row.get("memo"),
        "accounting_field_selections": [],
    }
    return {
        "id": row["txn_id"],
        "amount": _dollars(cents),
        "currency_code": ccode,
        "original_transaction_amount": currency_amount(cents, ccode),
        "minor_unit_conversion_rate": 100,
        "state": row.get("state") or "CLEARED",
        "sync_status": row.get("sync_status") or "SYNCED",
        "card_id": row.get("card_id"),
        "card_present": bool(row.get("card_present")),
        "card_holder": {
            "user_id": row.get("user_id"),
            "first_name": (row.get("cardholder_name") or " ").split(" ")[0],
            "last_name": (row.get("cardholder_name") or "  ").split(" ")[-1],
            "department_id": row.get("department_id") if "department_id" in row else None,
            "department_name": None,
            "location_id": None,
            "location_name": None,
            "employee_id": None,
        },
        "merchant_id": row.get("merchant_id"),
        "merchant_name": row.get("merchant_name"),
        "merchant_category_code": row.get("merchant_category_code"),
        "sk_category_id": row.get("sk_category_id"),
        "sk_category_name": row.get("sk_category_name"),
        "memo": row.get("memo"),
        "entity_id": row.get("entity_id"),
        "user_transaction_time": _dt(row.get("user_transaction_time")),
        "accounting_date": _dt(row.get("accounting_date")),
        "settlement_date": _dt(row.get("settlement_date")),
        "synced_at": _dt(row.get("synced_at")),
        "line_items": [line_item],
        "receipts": [],
        "policy_violations": [],
        "disputes": [],
        "accounting_field_selections": [],
    }


def reimbursement_dto(row: dict) -> dict[str, Any]:
    """Project an ``app_ramp.reimbursements`` row into a Ramp Reimbursement."""
    cents = row.get("amount_cents")
    cents = int(cents) if cents is not None else None
    ccode = row.get("currency") or "USD"
    return {
        "id": row["reimb_id"],
        "amount": _dollars(cents),
        "currency": ccode,  # NB: `currency`, NOT `currency_code` (reimbursement quirk)
        "original_reimbursement_amount": currency_amount(cents, ccode),
        "payee_amount": currency_amount(cents, ccode),
        "state": row.get("state") or "REIMBURSED",
        "type": row.get("type") or "OUT_OF_POCKET",
        "direction": row.get("direction") or "BUSINESS_TO_USER",
        "user_id": row.get("user_id"),
        "user_email": row.get("user_email"),
        "user_full_name": row.get("user_full_name"),
        "merchant": row.get("merchant"),
        "merchant_id": row.get("merchant_id"),
        "transaction_date": _date(row.get("transaction_date")),
        "created_at": _dt(row.get("created_at")),
        "updated_at": _dt(row.get("updated_at")),
        "submitted_at": _dt(row.get("submitted_at")),
        "approved_at": _dt(row.get("approved_at")),
        "synced_at": _dt(row.get("synced_at")),
        "sync_status": row.get("sync_status") or "SYNCED",
        "memo": row.get("memo"),
        "receipts": [],
        "line_items": [],
        "entity_id": None,
    }


def card_dto(row: dict) -> dict[str, Any]:
    """Project an ``app_ramp.cards`` row into a Ramp Card."""
    return {
        "id": row["card_id"],
        "display_name": row.get("display_name") or "",
        "last_four": row.get("last_four") or "0000",
        "cardholder_id": row.get("cardholder_id"),
        "cardholder_name": row.get("cardholder_name"),
        "card_program_id": row.get("card_program_id"),
        "entity_id": row.get("entity_id"),
        "expiration": row.get("expiration"),
        "is_physical": bool(row.get("is_physical")),
        "state": row.get("state") or "ACTIVE",
        "has_program_overridden": False,
        "created_at": _dt(row.get("created_at")),
    }


def user_dto(row: dict) -> dict[str, Any]:
    """Project an ``app_ramp.users`` row into a Ramp User."""
    return {
        "id": row["user_id"],
        "first_name": row.get("first_name") or "",
        "last_name": row.get("last_name") or "",
        "email": row.get("email") or "",
        "role": row.get("role") or "BUSINESS_USER",
        "status": row.get("status") or "USER_ACTIVE",
        "department_id": row.get("department_id"),
        "location_id": row.get("location_id"),
        "manager_id": row.get("manager_id"),
        "is_manager": bool(row.get("is_manager")),
        "employee_id": row.get("employee_id"),
        "business_id": row.get("business_id"),
        "entity_id": row.get("entity_id"),
        "phone": None,
    }


# Wire-level enums (for the seed, tests + the historical/live contract).
TRANSACTION_STATES = {
    "CLEARED", "COMPLETION", "DECLINED", "ERROR", "PENDING", "PENDING_INITIATION",
}
SYNC_STATUSES = {"NOT_SYNC_READY", "SYNCED", "SYNC_READY"}
REIMBURSEMENT_TYPES = {
    "MILEAGE", "OUT_OF_POCKET", "PAYBACK_FULL", "PAYBACK_PARTIAL", "PER_DIEM",
}
CARD_STATES = {"ACTIVE", "CHIP_LOCKED", "SUSPENDED", "TERMINATED", "UNACTIVATED"}
# Webhook event types relevant to the transaction stream (the full enum is larger).
WEBHOOK_EVENT_TYPES = {
    "transactions.authorized", "transactions.cleared", "transactions.declined",
    "transactions.ready_for_review", "transactions.ready_to_sync",
    "transactions.sync_requested", "transactions.synced",
}
