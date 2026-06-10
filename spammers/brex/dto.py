"""Brex account + transaction JSON shapes (the REAL api.brex.com ``/v2/`` contract).

Pinned against Brex's official OpenAPI (developer.brex.com/_spec/openapi/
transactions_api.yaml). The load-bearing facts:

  * **Money is an OBJECT ``{amount, currency}`` with ``amount`` an INTEGER in the
    smallest currency unit (CENTS for USD)** — emitted verbatim, NOT divided into
    dollars (the opposite of mercury). ``amount`` is SIGNED; ``currency`` is an
    ISO-4217 string defaulting to ``"USD"`` (nullable).
  * **Transaction date fields are DATE-ONLY ``YYYY-MM-DD``** (``format: date``) —
    ``initiated_at_date`` / ``posted_at_date`` carry NO time component (only the
    request filter ``posted_at_start`` is a date-time).
  * **Two account families:** ``CashAccount`` (name/account_number/routing_number/
    primary + balances) vs ``CardAccount`` (account_limit + current_statement_period,
    no name/account_number). **Two transaction families:** ``CashTransaction``
    (transfer_id) vs ``CardTransaction`` (card_id/merchant/expense_id).
  * List envelopes are ``Page_X_ = {next_cursor, items:[…]}`` — ``items`` required,
    ``next_cursor`` nullable (null/absent == last page). EXCEPT ``GET /v2/accounts/
    card`` which is a BARE ARRAY (no pagination).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional


def _date(dt: Optional[datetime]) -> Optional[str]:
    """A Brex ``format: date`` field — ``YYYY-MM-DD`` (UTC calendar date, no time)."""
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).date().isoformat()
    if isinstance(dt, date):
        return dt.isoformat()
    return None


def _money(cents: Optional[int], currency: Optional[str]) -> Optional[dict[str, Any]]:
    """A Brex ``Money`` — ``{amount: <int cents, signed>, currency: <ISO-4217>}``.

    ``None`` cents → ``null`` (Money is nullable on CashTransaction / CardAccount)."""
    if cents is None:
        return None
    return {"amount": int(cents), "currency": currency or "USD"}


def cash_account_dto(row: dict) -> dict[str, Any]:
    """Project an ``app_brex.accounts`` (kind='cash') row into a ``CashAccount``."""
    return {
        "id": row["account_id"],
        "name": row.get("name") or "",
        "status": row["status"],
        "current_balance": _money(row.get("current_balance_cents"), row.get("currency")),
        "available_balance": _money(row.get("available_balance_cents"), row.get("currency")),
        "account_number": row.get("account_number") or "",
        "routing_number": row.get("routing_number") or "",
        "primary": bool(row.get("is_primary")),
    }


def card_account_dto(row: dict) -> dict[str, Any]:
    """Project an ``app_brex.accounts`` (kind='card') row into a ``CardAccount``."""
    return {
        "id": row["account_id"],
        "status": row["status"],
        "current_balance": _money(row.get("current_balance_cents"), row.get("currency")),
        "available_balance": _money(row.get("available_balance_cents"), row.get("currency")),
        "account_limit": _money(row.get("account_limit_cents"), row.get("currency")),
        "current_statement_period": {
            "start_date": _date(row.get("statement_start")),
            "end_date": _date(row.get("statement_end")),
        },
    }


def cash_transaction_dto(row: dict) -> dict[str, Any]:
    """Project a cash ``app_brex.transactions`` row into a ``CashTransaction``."""
    return {
        "id": row["txn_id"],
        "description": row.get("description") or "",
        "amount": _money(row.get("amount_cents"), row.get("currency")),
        "initiated_at_date": _date(row["initiated_at"]),
        "posted_at_date": _date(row["posted_at"]),
        "type": row.get("txn_type"),
        "transfer_id": row.get("transfer_id"),
    }


def card_transaction_dto(row: dict) -> dict[str, Any]:
    """Project a card ``app_brex.transactions`` row into a ``CardTransaction``."""
    merchant = None
    if row.get("merchant_raw_descriptor"):
        merchant = {
            "raw_descriptor": row.get("merchant_raw_descriptor"),
            "mcc": row.get("merchant_mcc"),
            "country": row.get("merchant_country"),
        }
    return {
        "id": row["txn_id"],
        "card_id": row.get("card_id"),
        "description": row.get("description") or "",
        "amount": _money(row.get("amount_cents"), row.get("currency")),
        "initiated_at_date": _date(row["initiated_at"]),
        "posted_at_date": _date(row["posted_at"]),
        "type": row.get("txn_type"),
        "merchant": merchant,
        "expense_id": row.get("expense_id"),
    }


# Wire-level enums (for the seed, tests + the historical/live contract).
ACCOUNT_STATUSES = {"ACTIVE"}
CASH_TXN_TYPES = {
    "PAYMENT", "DIVIDEND", "FEE", "ADJUSTMENT", "INTEREST", "CARD_COLLECTION",
    "REWARDS_REDEMPTION", "RECEIVABLES_OFFERS_ADVANCE", "FBO_TRANSFER",
    "RECEIVABLES_OFFERS_REPAYMENT", "RECEIVABLES_OFFERS_COLLECTION",
    "BREX_OPERATIONAL_TRANSFER", "INTRA_CUSTOMER_ACCOUNT_BOOK_TRANSFER",
    "BOOK_TRANSFER", "CRYPTO_BRIDGE", "STABLECOIN", "TRANSACTION_FEES_COLLECTION",
    "PAYBACK",
}
CARD_TXN_TYPES = {
    "PURCHASE", "REFUND", "CHARGEBACK", "REWARDS_CREDIT", "COLLECTION", "BNPL_FEE",
}
# Webhook event types relevant to money movement (the full enum is larger).
WEBHOOK_EVENT_TYPES = {"TRANSFER_PROCESSED", "TRANSFER_FAILED"}
