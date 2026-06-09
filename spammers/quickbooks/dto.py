"""QuickBooks Online v3 entity shapes.

Fyralis ingests the four transactional entities **Invoice, Bill, BillPayment,
Payment** via the ``query`` endpoint. The mock's corpus models the company's
finances as ``purchases`` (vendor money-out, AP) and ``deposits`` (money-in;
``round_kind='grant'`` is billable revenue, AR). We project those into the QBO
entity shapes at read time — faithful to what a real QBO connector sees:

  * purchase            -> Bill (the vendor obligation) + BillPayment (settling it)
  * deposit(grant)      -> Invoice (billed to the grantor) + Payment (the receipt)

Equity rounds (seed/strategic/founders) are NOT AR and stay out of Invoice — a
research lab's AR is just its grants, which is realistic and still exercises all
four query paths. Every entity carries QBO's ``Id``/``SyncToken``/``MetaData``;
amounts are cents -> decimal-2; timestamps are RFC3339 with an offset.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

_AP_ACCOUNT = {"value": "2000", "name": "Accounts Payable (A/P)"}
_USD = {"value": "USD", "name": "United States Dollar"}


def _offset_iso(dt: Optional[datetime]) -> Optional[str]:
    """RFC3339 with a timezone offset, e.g. 2018-03-13T16:33:42-07:00 (QBO MetaData)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    s = dt.isoformat(timespec="seconds")
    return s.replace("+00:00", "+00:00")  # keep the explicit offset


def _date(d: Any) -> Optional[str]:
    if d is None:
        return None
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


def _money(cents: Optional[int]) -> float:
    return round((cents or 0) / 100, 2)


def _sync_token(row: dict) -> str:
    # SyncToken is QBO's per-entity version counter. The mock models txn updates
    # via a `version` (default 0); a live update bumps it so a re-query re-observes.
    return str(row.get("version", 0) or 0)


def _metadata(row: dict) -> dict[str, Any]:
    created = _offset_iso(row.get("created_at"))
    updated = _offset_iso(row.get("updated_at") or row.get("created_at"))
    return {"CreateTime": created, "LastUpdatedTime": updated}


def _customer_ref(lead: Optional[str]) -> dict[str, str]:
    name = (lead or "Grant").strip() or "Grant"
    return {"value": str(abs(hash(name)) % 9_000_000 + 1_000_000), "name": name}


def company_info_dto(row: dict) -> dict[str, Any]:
    return {
        "Id": row["realm_id"],
        "SyncToken": _sync_token(row),
        "domain": "QBO",
        "sparse": False,
        "CompanyName": row["company_name"],
        "LegalName": row["legal_name"],
        "Country": row["country"],
        "CompanyAddr": {"Id": "1", "Country": row["country"]},
        "FiscalYearStartMonth": row.get("fiscal_year_start") or "January",
        "DefaultCurrencyRef": {"value": row.get("currency") or "USD"},
        "MetaData": _metadata(row),
    }


def bill_dto(row: dict) -> dict[str, Any]:
    """A purchase row -> a QBO Bill (the vendor's AP obligation)."""
    amt = _money(row["amount_cents"])
    return {
        "Id": row["purchase_id"],
        "SyncToken": _sync_token(row),
        "domain": "QBO",
        "sparse": False,
        "TxnDate": _date(row["txn_date"]),
        "DueDate": _date(row["txn_date"]),
        "VendorRef": {"value": row.get("vendor_id") or "0",
                      "name": row.get("vendor_name") or "Unknown Vendor"},
        "APAccountRef": dict(_AP_ACCOUNT),
        "Line": [{
            "Id": "1",
            "Amount": amt,
            "DetailType": "AccountBasedExpenseLineDetail",
            "AccountBasedExpenseLineDetail": {
                "AccountRef": {"value": row.get("expense_acct_num") or "5000",
                               "name": row.get("expense_acct_name") or "Expense"},
            },
        }],
        "TotalAmt": amt,
        "Balance": 0.0,  # settled by the BillPayment below
        "CurrencyRef": dict(_USD),
        "MetaData": _metadata(row),
    }


def bill_payment_dto(row: dict) -> dict[str, Any]:
    """A purchase row -> the BillPayment that settles its Bill (PayType=Check)."""
    amt = _money(row["amount_cents"])
    return {
        "Id": f"BP-{row['purchase_id']}",
        "SyncToken": _sync_token(row),
        "domain": "QBO",
        "sparse": False,
        "TxnDate": _date(row["txn_date"]),
        "VendorRef": {"value": row.get("vendor_id") or "0",
                      "name": row.get("vendor_name") or "Unknown Vendor"},
        "TotalAmt": amt,
        "PayType": "Check",
        "CheckPayment": {
            "BankAccountRef": {"value": row.get("pay_acct_num") or "1000",
                               "name": row.get("pay_acct_name") or "Checking"},
            "PrintStatus": "NotSet",
        },
        "APAccountRef": dict(_AP_ACCOUNT),
        "Line": [{
            "Amount": amt,
            "LinkedTxn": [{"TxnId": row["purchase_id"], "TxnType": "Bill"}],
        }],
        "CurrencyRef": dict(_USD),
        "MetaData": _metadata(row),
    }


def invoice_dto(row: dict) -> dict[str, Any]:
    """A grant deposit row -> a QBO Invoice billed to the grantor (AR)."""
    amt = _money(row["amount_cents"])
    return {
        "Id": row["deposit_id"],
        "SyncToken": _sync_token(row),
        "domain": "QBO",
        "sparse": False,
        "TxnDate": _date(row["txn_date"]),
        "DueDate": _date(row["txn_date"]),
        "DocNumber": f"INV-{row['deposit_id']}",
        "CustomerRef": _customer_ref(row.get("lead")),
        "Line": [{
            "Id": "1",
            "Amount": amt,
            "DetailType": "SalesItemLineDetail",
            "SalesItemLineDetail": {
                "ItemRef": {"value": "1", "name": "Grant Revenue"},
                "Qty": 1,
                "UnitPrice": amt,
            },
        }],
        "TotalAmt": amt,
        "Balance": 0.0,  # paid (Payment below)
        "CurrencyRef": dict(_USD),
        "MetaData": _metadata(row),
    }


def payment_dto(row: dict) -> dict[str, Any]:
    """A grant deposit row -> the Payment receipt applied to its Invoice (AR)."""
    amt = _money(row["amount_cents"])
    return {
        "Id": f"P-{row['deposit_id']}",
        "SyncToken": _sync_token(row),
        "domain": "QBO",
        "sparse": False,
        "TxnDate": _date(row["txn_date"]),
        "CustomerRef": _customer_ref(row.get("lead")),
        "TotalAmt": amt,
        "UnappliedAmt": 0.0,
        "Line": [{
            "Amount": amt,
            "LinkedTxn": [{"TxnId": row["deposit_id"], "TxnType": "Invoice"}],
        }],
        "DepositToAccountRef": {"value": row.get("dep_acct_num") or "1000",
                                "name": row.get("dep_acct_name") or "Checking"},
        "CurrencyRef": dict(_USD),
        "MetaData": _metadata(row),
    }
