"""A minimal QBO SQL parser — enough for what a connector actually sends.

The QBO ``query`` endpoint takes a SQL-like string. Fyralis (and most connectors)
send only these shapes per entity:

    SELECT * FROM Invoice STARTPOSITION 1 MAXRESULTS 100                  (backfill)
    SELECT * FROM Bill WHERE Metadata.LastUpdatedTime > '<ts>'            (incremental)
            ORDERBY Metadata.LastUpdatedTime STARTPOSITION 1 MAXRESULTS 100
    SELECT COUNT(*) FROM Payment                                          (count probe)

We extract: the entity, an optional ``Metadata.LastUpdatedTime`` floor, whether it
is a COUNT(*), and the 1-based STARTPOSITION + MAXRESULTS. Anything richer is not
modelled (a real connector falls back gracefully).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

_FROM = re.compile(r"\bfrom\s+([A-Za-z]+)", re.IGNORECASE)
_COUNT = re.compile(r"select\s+count\s*\(\s*\*\s*\)", re.IGNORECASE)
_UPDATED = re.compile(
    r"metadata\.lastupdatedtime\s*(>=|>)\s*'([^']+)'", re.IGNORECASE)
_STARTPOS = re.compile(r"\bstartposition\s+(\d+)", re.IGNORECASE)
_MAXRESULTS = re.compile(r"\bmaxresults\s+(\d+)", re.IGNORECASE)
_ID_EQ = re.compile(r"\bid\s*=\s*'([^']+)'", re.IGNORECASE)

# Entity name (as it appears in the query / response key) -> canonical key.
_ENTITIES = {e.lower(): e for e in
             ("Invoice", "Bill", "BillPayment", "Payment", "CompanyInfo")}


@dataclass
class QboQuery:
    entity: Optional[str]          # canonical entity name, or None if unknown
    is_count: bool
    updated_after: Optional[datetime]
    id_equals: Optional[str]       # WHERE Id = '...' (single-entity fetch-back)
    start_position: int            # 1-based
    max_results: int


def _parse_ts(s: str) -> Optional[datetime]:
    s = s.strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        # QBO also accepts a bare date.
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
    return None


def parse_query(q: str) -> QboQuery:
    q = q or ""
    fm = _FROM.search(q)
    entity = _ENTITIES.get(fm.group(1).lower()) if fm else None
    um = _UPDATED.search(q)
    idm = _ID_EQ.search(q)
    sp = _STARTPOS.search(q)
    mr = _MAXRESULTS.search(q)
    try:
        start = max(1, int(sp.group(1))) if sp else 1
    except ValueError:
        start = 1
    try:
        # QBO default 100, hard max 1000.
        mx = max(1, min(int(mr.group(1)), 1000)) if mr else 100
    except ValueError:
        mx = 100
    return QboQuery(
        entity=entity,
        is_count=bool(_COUNT.search(q)),
        updated_after=_parse_ts(um.group(2)) if um else None,
        id_equals=idm.group(1) if idm else None,
        start_position=start,
        max_results=mx,
    )
