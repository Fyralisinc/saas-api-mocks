"""Stream events from a ``events.jsonl`` file in time order.

The renderer in ``alpen-corpus`` already sorts on write, so we don't re-sort
here — just verify monotonicity as a defensive guard and yield in file order.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from spammers.corpus.schema import Event, SchemaError, validate


def _parse_ts(t: str) -> datetime:
    dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def iter_events(
    path: str | Path,
    *,
    until: datetime | None = None,
    after: datetime | None = None,
) -> Iterator[tuple[datetime, Event]]:
    """Yield ``(timestamp, event)`` pairs from the corpus.

    - ``until``: stop after the first event with ``t > until`` (inclusive replay up to cursor).
    - ``after``: skip events with ``t <= after`` (resume from a prior cursor).
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"corpus events file not found: {path}")
    last_ts: datetime | None = None
    with path.open() as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as e:
                raise SchemaError(f"invalid JSON on line {line_no}: {e}")
            event = validate(raw, line_no=line_no)
            ts = _parse_ts(event["t"])
            if last_ts is not None and ts < last_ts:
                raise SchemaError(
                    f"events.jsonl out of order at line {line_no}: {ts} < {last_ts}"
                )
            last_ts = ts
            if after is not None and ts <= after:
                continue
            if until is not None and ts > until:
                return
            yield ts, event


def count_events(path: str | Path) -> dict[str, int]:
    """Quick scan: total + per-provider counts. For status displays."""
    by_provider: dict[str, int] = {}
    total = 0
    for _, ev in iter_events(path):
        by_provider[ev["provider"]] = by_provider.get(ev["provider"], 0) + 1
        total += 1
    return {"total": total, **by_provider}
