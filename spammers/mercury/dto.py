"""Mercury account + transaction JSON shapes.

Built from Mercury's embedded OpenAPI (docs.mercury.com). Two key money facts:

  * **Amounts are DOLLARS**, not cents — JSON numbers with ``multipleOf 0.01``
    (the mock stores cents and divides here). The transaction ``amount`` is
    **signed**: negative == debit/outflow, positive == credit/inflow.
  * **Timestamps are RFC3339 UTC ``…Z`` at SECONDS precision** (e.g.
    ``2016-07-22T00:00:00Z``) — no fractional seconds (the webhook's ``occurredAt``
    uses microseconds; that's a separate path, see webhooks.py).

The ``Account`` is the schema returned by ``/accounts`` (inside the list
envelope) and by ``/account/{id}`` (bare). The ``Transaction`` is returned inside
``/account/{id}/transactions`` and bare by ``/account/{id}/transaction/{id}``.
Required fields are always present; nullable optionals are emitted as ``null``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def _utc_z(dt: Optional[datetime]) -> Optional[str]:
    """RFC3339 UTC, seconds precision, ``Z`` suffix — Mercury's ``UTCTime``."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _money(cents: Optional[int]) -> float:
    """Cents -> signed decimal dollars (the wire's ``number, multipleOf 0.01``)."""
    return round((cents or 0) / 100, 2)


def account_dto(row: dict) -> dict[str, Any]:
    """Project an ``app_mercury.accounts`` row into a Mercury ``Account``."""
    return {
        "id": str(row["account_id"]),
        "accountNumber": row["account_number"],
        "routingNumber": row["routing_number"],
        "name": row["name"],
        "nickname": row.get("nickname"),
        "status": row["status"],
        "type": row["type"],
        "createdAt": _utc_z(row["created_at"]),
        "availableBalance": _money(row["available_balance_cents"]),
        "currentBalance": _money(row["current_balance_cents"]),
        "kind": row["kind"],
        "legalBusinessName": row["legal_business_name"],
        "dashboardLink": row.get("dashboard_link") or "",
        "canReceiveTransactions": row.get("can_receive_transactions"),
    }


def transaction_dto(row: dict) -> dict[str, Any]:
    """Project an ``app_mercury.transactions`` row into a Mercury ``Transaction``.

    Every ``required`` field is present; the common optionals are emitted as null
    (or their value). The required arrays ``glAllocations``/``attachments``/
    ``relatedTransactions`` are empty by default.
    """
    cp_id = row.get("counterparty_id")
    return {
        "id": str(row["txn_id"]),
        "amount": _money(row["amount_cents"]),
        "status": row["status"],
        "kind": row["kind"],
        "createdAt": _utc_z(row["created_at"]),
        "postedAt": _utc_z(row.get("posted_at")),
        "estimatedDeliveryDate": _utc_z(row["estimated_delivery_date"]),
        "failedAt": _utc_z(row.get("failed_at")),
        "counterpartyId": str(cp_id) if cp_id else None,
        "counterpartyName": row.get("counterparty_name") or "",
        "counterpartyNickname": row.get("counterparty_nickname"),
        "accountId": str(row["account_id"]),
        "note": row.get("note"),
        "externalMemo": row.get("external_memo"),
        "bankDescription": row.get("bank_description"),
        "reasonForFailure": row.get("reason_for_failure"),
        "feeId": None,
        "checkNumber": row.get("check_number"),
        "trackingNumber": None,
        "requestId": None,
        "dashboardLink": row.get("dashboard_link") or "",
        "details": None,
        "mercuryCategory": None,
        "categoryData": None,
        "merchant": None,
        "currencyExchangeInfo": None,
        "creditAccountPeriodId": None,
        "compliantWithReceiptPolicy": True,
        "hasGeneratedReceipt": False,
        "generalLedgerCodeName": None,
        "glAllocations": [],
        "attachments": [],
        "relatedTransactions": [],
    }


# Wire-level enums (for tests + the live/historical contract).
ACCOUNT_STATUSES = {"active", "deleted", "pending", "archived"}
ACCOUNT_TYPES = {"mercury", "external", "recipient"}
TXN_STATUSES = {"pending", "sent", "cancelled", "failed", "reversed", "blocked"}
TXN_KINDS = {
    "externalTransfer", "internalTransfer", "outgoingPayment", "creditCardCredit",
    "creditCardTransaction", "debitCardCredit", "debitCardTransaction",
    "cardInternationalTransactionFee", "cardInternationalTransactionFeeRebate",
    "cardInternationalTransactionFeeReversal",
    "cardInternationalTransactionFeeRebateReversal", "incomingDomesticWire",
    "checkDeposit", "incomingInternationalWire", "treasuryTransfer",
    "currencyCloudReturn", "wireFee", "personalBankingSubscriptionFee",
    "billingEngineSubscriptionFee", "expenseReimbursement",
    "exogenousWireDrawdown", "other",
}
