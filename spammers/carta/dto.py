"""Carta object JSON shapes (the REAL api.carta.com ``/v1alpha1`` contract).

Pinned against docs.carta.com (the per-endpoint reference pages, generated from
Carta's OpenAPI). The single most load-bearing fidelity fact:

  **Money + every decimal/quantity is a PROTOBUF WRAPPER object whose ``value`` is a
  decimal STRING — NOT a number, NOT integer cents.**

    * Money    = ``{"currencyCode":{"value":"USD"}, "amount":{"value":"20261050.79"}}``
    * Decimal  = ``{"value":"61399310.00"}``  (share counts, rates, percentages)

Other load-bearing facts a faithful consumer depends on:
  * IDs are MIXED: the issuer suite uses SHORT NUMERIC-STRING ids ("611"); the
    cross-ref ``securityId``/``shareClassId`` are UUIDs.
  * Timestamps are RFC3339 UTC with ``Z`` + MICROSECONDS (``2024-07-30T09:31:57.000000Z``);
    pure dates are ``YYYY-MM-DD``.
  * Single-object GETs wrap under a SINGULAR key (``{issuer:{…}}``); list endpoints
    wrap the list under a PLURAL key alongside ``nextPageToken``.
  * There is NO SyncToken (a QBO-archetype field the Fyralis client expects); the
    securities carry ``lastModifiedDatetime`` to version on instead.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional


# --------------------------------------------------------------- wire primitives

def dt_us_z(dt: Optional[datetime]) -> Optional[str]:
    """Carta's datetime wire form: RFC3339 UTC, **microsecond** precision, ``Z``."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond:06d}Z"


def date_str(d) -> Optional[str]:
    """A Carta date field — ``YYYY-MM-DD`` (UTC calendar date, no time)."""
    if d is None:
        return None
    if isinstance(d, datetime):
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    return str(d)


def decimal_value(raw: Any, *, places: int = 2) -> Optional[dict[str, str]]:
    """A Carta bare-decimal wrapper — ``{"value":"<decimal string>"}``.

    Accepts an int/whole share count (formatted to ``places`` decimals) or a
    decimal STRING (passed through verbatim, since Carta keeps the author's
    precision). Used for share counts, quantities, rates and percentages."""
    if raw is None:
        return None
    if isinstance(raw, str):
        return {"value": raw}
    if isinstance(raw, bool):  # guard: bool is an int subclass
        return None
    if isinstance(raw, (int, Decimal, float)):
        return {"value": f"{Decimal(str(raw)):.{places}f}"}
    return {"value": str(raw)}


def money(cents: Optional[int], currency_code: str = "USD") -> Optional[dict[str, Any]]:
    """A Carta Money wrapper — ``{"currencyCode":{"value":"USD"}, "amount":{"value":
    "<decimal string>"}}`` (the amount is dollars, NOT cents, as a 2-dp string)."""
    if cents is None:
        return None
    amount = f"{Decimal(cents) / 100:.2f}"
    return {"currencyCode": {"value": currency_code or "USD"},
            "amount": {"value": amount}}


def money_str(amount_str: Optional[str], currency_code: str = "USD") -> Optional[dict[str, Any]]:
    """A Carta Money wrapper from a decimal STRING amount (e.g. a par value
    ``"0.0001"`` we keep verbatim rather than rounding through cents)."""
    if amount_str is None:
        return None
    return {"currencyCode": {"value": currency_code or "USD"},
            "amount": {"value": amount_str}}


# ------------------------------------------------------------------- entity DTOs

def issuer_dto(row: dict) -> dict[str, Any]:
    """An Issuer — ``{id (numeric string), legalName, doingBusinessAsName, website}``."""
    return {
        "id": row["issuer_id"],
        "legalName": row["legal_name"],
        "doingBusinessAsName": row.get("doing_business_as_name") or "",
        "website": row.get("website") or "",
    }


def stakeholder_dto(row: dict) -> dict[str, Any]:
    """A Stakeholder — fullName/email/relationship/entityType + address.country."""
    out: dict[str, Any] = {
        "id": row["stakeholder_id"],
        "issuerId": row["issuer_id"],
        "fullName": row["full_name"],
        "email": row.get("email") or "",
        "relationship": row.get("relationship") or "OTHER",
        "entityType": row.get("entity_type") or "INDIVIDUAL",
        "address": {"country": row.get("country") or "US"},
    }
    if row.get("employee_id"):
        out["employeeId"] = row["employee_id"]
    if row.get("grp"):
        out["group"] = row["grp"]
    return out


def share_class_dto(row: dict) -> dict[str, Any]:
    """A ShareClass — name/prefix/type + authorizedShareCount (decimal wrapper) +
    parValue (Money) + seniority (int) + pariPassu (bool)."""
    return {
        "id": row["share_class_id"],
        "issuerId": row["issuer_id"],
        "name": row["name"],
        "prefix": row["prefix"],
        "type": row["type"],
        "authorizedShareCount": decimal_value(int(row["authorized_shares"])),
        "parValue": money_str(row.get("par_value"), row.get("currency_code") or "USD"),
        "seniority": int(row.get("seniority") or 0),
        "pariPassu": bool(row.get("pari_passu")),
    }


def option_grant_dto(row: dict) -> dict[str, Any]:
    """An OptionGrant — numeric-string ``id`` + UUID ``securityId``; quantities are
    decimal wrappers, ``exercisePrice`` is Money, ``lastModifiedDatetime`` is
    RFC3339-µs-Z. NB: there is NO grant-level status enum (status lives on nested
    exercises) and NO SyncToken."""
    return {
        "id": row["grant_id"],
        "securityId": row["security_id"],
        "shareClassId": row.get("share_class_id"),
        "issuerId": row["issuer_id"],
        "stakeholderId": row["stakeholder_id"],
        "equityIncentivePlanName": row.get("plan_name") or "Equity Incentive Plan",
        "stockOptionType": row.get("stock_option_type") or "ISO",
        "quantity": decimal_value(int(row["quantity"])),
        "vestedQuantity": decimal_value(int(row.get("vested_quantity") or 0)),
        "exercisedQuantity": decimal_value(int(row.get("exercised_quantity") or 0)),
        "exercisePrice": money_str(row.get("exercise_price"),
                                   row.get("currency_code") or "USD"),
        "earlyExercisable": bool(row.get("early_exercisable")),
        "issueDate": date_str(row.get("issue_date")),
        "vestingStartDate": date_str(row.get("vesting_start_date")),
        "grantExpirationDate": date_str(row.get("grant_expiration_date")),
        "vestingEvents": [],
        "exercises": [],
        "lastModifiedDatetime": dt_us_z(row.get("last_modified")),
    }


def convertible_note_dto(row: dict) -> dict[str, Any]:
    """A ConvertibleNote (SAFEs live here) — Money cashPaid/priceCap + decimal-wrapper
    interestRate/discountPercentage + RFC3339-µs-Z issue/maturity datetimes."""
    ccode = row.get("currency_code") or "USD"
    cap = row.get("price_cap_cents")
    return {
        "id": row["note_id"],
        "securityId": row["security_id"],
        "issuerId": row["issuer_id"],
        "stakeholderId": row["stakeholder_id"],
        "securityLabel": row["security_label"],
        "cashPaid": money(int(row["cash_paid_cents"]), ccode),
        "priceCap": money(int(cap), ccode) if cap is not None else None,
        "interestRate": decimal_value(row.get("interest_rate")),
        "discountPercentage": decimal_value(row.get("discount_percentage")),
        "interestCompoundingPeriod": row.get("interest_compounding_period") or "ANNUALLY",
        "dayCountBasis": row.get("day_count_basis") or "COUNT_ACTUAL_365",
        "issueDatetime": dt_us_z(row.get("issue_datetime")),
        "maturityDatetime": dt_us_z(row.get("maturity_datetime")),
        "lastModifiedDatetime": dt_us_z(row.get("last_modified")),
    }


# ---------------------------------------------------------------- wire-level enums
# (for the seed + tests + the historical contract.)
RELATIONSHIPS = {
    "ADVISOR", "EX_ADVISOR", "BOARD_MEMBER", "CONSULTANT", "EX_CONSULTANT",
    "EMPLOYEE", "EX_EMPLOYEE", "EXECUTIVE", "FOUNDER", "INTERNATIONAL_EMPLOYEE",
    "INVESTOR", "OFFICER", "OTHER", "EX_BOARD_MEMBER", "EX_INTERNATIONAL_EMPLOYEE",
}
ENTITY_TYPES = {
    "INDIVIDUAL", "CORPORATION", "LIMITED_LIABILITY_CORPORATION", "ESTATE_OR_TRUST",
    "PARTNERSHIP", "DISREGARDED_ENTITY", "UNKNOWN",
}
SHARE_CLASS_TYPES = {"COMMON", "PREFERRED"}
STOCK_OPTION_TYPES = {"ISO", "NSO", "OTHER"}
INTEREST_COMPOUNDING_PERIODS = {
    "SIMPLE", "DAILY", "MONTHLY", "SEMI_ANNUALLY", "ANNUALLY",
}
DAY_COUNT_BASES = {"COUNT_30_360", "COUNT_ACTUAL_360", "COUNT_ACTUAL_365"}

# Per-endpoint AIP pagination caps (default pageSize is 25 everywhere).
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = {
    "stakeholders": 100,
    "shareClasses": 50,
    "optionGrants": 50,
    "convertibleNotes": 50,
    "issuers": 50,
}
