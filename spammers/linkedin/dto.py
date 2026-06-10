"""LinkedIn object JSON shapes (the REAL api.linkedin.com ``/rest/`` contract).

Pinned against the official Community Management docs (learn.microsoft.com/linkedin:
Posts API, Organization Share Statistics, Organization Follower Statistics). The
load-bearing fidelity facts a consumer that also works against real LinkedIn depends
on:

  * A POST element ``id`` is a URN — ``urn:li:share:{n}`` or ``urn:li:ugcPost:{n}``;
    ``author`` is the org URN ``urn:li:organization:{n}``.
  * ``createdAt`` / ``lastModifiedAt`` / ``publishedAt`` are epoch-MILLIS INTEGERS
    (e.g. 1634817395768) — NOT ISO strings.
  * Collections are Rest.li FINDER envelopes ``{elements:[…], paging:{start,count,
    links:[…]}}``; ``paging`` on the org endpoints carries ``start``+``count`` and an
    (often empty) ``links`` array.
  * ``totalShareStatistics`` has exactly seven counters (+ ``engagement`` double);
    the follower-stats facet arrays each hold ``{<segmentKey>, followerCounts:
    {organicFollowerCount, paidFollowerCount}}`` and there is NO lifetime total.
"""
from __future__ import annotations

import json
from typing import Any


def _jsonb(val: Any) -> Any:
    """asyncpg returns jsonb columns as a str (no codec registered) — decode it."""
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (ValueError, json.JSONDecodeError):
            return []
    return val if val is not None else []


# ------------------------------------------------------------------- URN helpers

def org_urn(org_id: int) -> str:
    return f"urn:li:organization:{org_id}"


def post_urn(urn_type: str, post_id: int) -> str:
    return f"urn:li:{urn_type}:{post_id}"


# ------------------------------------------------------------------- entity DTOs

def organization_dto(row: dict) -> dict[str, Any]:
    """An Organization (the ``GET /rest/organizations/{id}`` lookup / connectivity
    probe). The ``id`` is a bare INT; ``name`` is a localized-string struct."""
    name = row["localized_name"]
    return {
        "id": int(row["org_id"]),
        "localizedName": name,
        "vanityName": row["vanity_name"],
        "localizedWebsite": row.get("website") or "",
        "name": {
            "localized": {"en_US": name},
            "preferredLocale": {"country": "US", "language": "en"},
        },
        "primaryOrganizationType": "NONE",
        "$URN": org_urn(int(row["org_id"])),
    }


def post_dto(row: dict, author_urn: str) -> dict[str, Any]:
    """A Post element (``GET /rest/posts``). ``id`` is the share/ugcPost URN;
    timestamps are epoch-MILLIS integers; ``distribution``/``content``/
    ``lifecycleStateInfo`` are the documented nested objects."""
    return {
        "id": post_urn(row["urn_type"], int(row["post_id"])),
        "author": author_urn,
        "commentary": row.get("commentary") or "",
        "visibility": row.get("visibility") or "PUBLIC",
        "lifecycleState": row.get("lifecycle_state") or "PUBLISHED",
        "lifecycleStateInfo": {"isEditedByAuthor": bool(row.get("is_edited"))},
        "isReshareDisabledByAuthor": bool(row.get("is_reshare_disabled")),
        "distribution": {
            "feedDistribution": row.get("feed_distribution") or "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "content": {},
        "createdAt": int(row["created_at_ms"]),
        "lastModifiedAt": int(row["last_modified_ms"]),
        "publishedAt": int(row["published_at_ms"]),
    }


def share_statistics_dto(row: dict, entity_urn: str) -> dict[str, Any]:
    """An organizationalEntityShareStatistics element — the lifetime aggregate.

    ``totalShareStatistics`` carries exactly the seven documented counters; the
    element also echoes the ``organizationalEntity`` URN it was scoped to."""
    return {
        "totalShareStatistics": {
            "uniqueImpressionsCount": int(row.get("unique_impressions_count") or 0),
            "clickCount": int(row.get("click_count") or 0),
            "engagement": float(row.get("engagement") or 0.0),
            "likeCount": int(row.get("like_count") or 0),
            "commentCount": int(row.get("comment_count") or 0),
            "shareCount": int(row.get("share_count") or 0),
            "impressionCount": int(row.get("impression_count") or 0),
        },
        "organizationalEntity": entity_urn,
    }


def follower_statistics_dto(row: dict, entity_urn: str) -> dict[str, Any]:
    """An organizationalEntityFollowerStatistics element — the lifetime facet
    breakdowns. Each facet is an array of ``{<segmentKey>, followerCounts}``; there
    is NO lifetime total on this endpoint (removed — use networkSizes)."""
    return {
        "followerCountsByAssociationType": _jsonb(row.get("by_association_type")),
        "followerCountsBySeniority": _jsonb(row.get("by_seniority")),
        "followerCountsByFunction": _jsonb(row.get("by_function")),
        "followerCountsByStaffCountRange": _jsonb(row.get("by_staff_count_range")),
        "followerCountsByGeoCountry": _jsonb(row.get("by_geo_country")),
        "followerCountsByGeo": _jsonb(row.get("by_geo")),
        "followerCountsByIndustry": _jsonb(row.get("by_industry")),
        "organizationalEntity": entity_urn,
    }


# ---------------------------------------------------------------- wire-level enums
# (for the seed + tests + the historical contract.)
LIFECYCLE_STATES = {"PUBLISHED", "DRAFT", "PUBLISH_REQUESTED", "PUBLISH_FAILED",
                    "PROCESSING"}
URN_TYPES = {"share", "ugcPost"}
ASSOCIATION_TYPES = {"EMPLOYEE", "MEMBER"}
STAFF_COUNT_RANGES = {
    "SIZE_1", "SIZE_2_TO_10", "SIZE_11_TO_50", "SIZE_51_TO_200", "SIZE_201_TO_500",
    "SIZE_501_TO_1000", "SIZE_1001_TO_5000", "SIZE_5001_TO_10000", "SIZE_10001_OR_MORE",
}

# Offset-pagination knobs for /rest/posts (the org stats finders don't paginate).
DEFAULT_COUNT = 10
MAX_COUNT = 100
