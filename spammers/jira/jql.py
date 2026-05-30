"""A minimal JQL parser — enough for what the consumer actually sends.

The Fyralis Jira fetcher/reconciler emit only these shapes:
  - ``project = "KEY" ORDER BY updated ASC``           (backfill)
  - ``project = "KEY" AND updated >= "yyyy/MM/dd HH:mm"``  (poll cursor + probe)

JQL date literals with ``HH:mm`` are **minute-precision** (a `[:00, :59]` range)
per Atlassian's docs, so ``>= "09:24"`` means ``>= 09:24:00`` — an issue at
09:24:29 matches. We parse the literal to its minute-start and compare against
the issue's full-precision ``updated``; ``>`` uses the minute-END (range upper
bound) so the operators stay faithful to real Jira.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

_PROJECT = re.compile(r'project\s*=\s*"?([A-Za-z0-9_]+)"?', re.IGNORECASE)
_UPDATED = re.compile(r'updated\s*(>=|<=|>|<)\s*"([^"]+)"', re.IGNORECASE)
_ORDER = re.compile(r'ORDER\s+BY\s+(\w+)\s+(ASC|DESC)', re.IGNORECASE)


@dataclass
class JqlQuery:
    project_key: Optional[str]
    updated_op: Optional[str]
    updated_dt: Optional[datetime]      # minute-start of the literal
    order_field: str = "updated"
    order_desc: bool = False


def _parse_literal(s: str) -> Optional[datetime]:
    s = s.strip()
    for fmt in ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M", "%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse_jql(jql: str) -> JqlQuery:
    jql = jql or ""
    pm = _PROJECT.search(jql)
    um = _UPDATED.search(jql)
    om = _ORDER.search(jql)
    updated_op = updated_dt = None
    if um:
        updated_op = um.group(1)
        updated_dt = _parse_literal(um.group(2))
    return JqlQuery(
        project_key=pm.group(1) if pm else None,
        updated_op=updated_op,
        updated_dt=updated_dt,
        order_field=(om.group(1).lower() if om else "updated"),
        order_desc=bool(om and om.group(2).upper() == "DESC"),
    )


def matches_updated(issue_updated: datetime, op: Optional[str], literal: Optional[datetime]) -> bool:
    """Faithful minute-precision comparison of ``updated <op> literal``."""
    if op is None or literal is None:
        return True
    if issue_updated.tzinfo is None:
        issue_updated = issue_updated.replace(tzinfo=timezone.utc)
    minute_start = literal
    minute_end = literal + timedelta(seconds=59, microseconds=999999)
    if op == ">=":
        return issue_updated >= minute_start
    if op == ">":
        return issue_updated > minute_end
    if op == "<=":
        return issue_updated <= minute_end
    if op == "<":
        return issue_updated < minute_start
    return True
