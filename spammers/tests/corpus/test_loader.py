"""Loader streams events in time order, validates schema, respects cursors."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from spammers.corpus.loader import iter_events, count_events
from spammers.corpus.schema import SchemaError, validate


def test_iter_events_yields_all_in_order(tiny_corpus):
    events = list(iter_events(tiny_corpus))
    assert len(events) == 6
    timestamps = [ts for ts, _ in events]
    assert timestamps == sorted(timestamps), "events must be in time order"


def test_iter_events_respects_until_cursor(tiny_corpus):
    cutoff = datetime(2024, 1, 15, 9, 0, 2, tzinfo=timezone.utc)
    events = list(iter_events(tiny_corpus, until=cutoff))
    # First 3 are <= cutoff (the team creates + alice).
    assert len(events) == 3
    assert events[-1][1]["payload"]["id"] == "person:alice"


def test_iter_events_resumes_from_after(tiny_corpus):
    after = datetime(2024, 1, 15, 9, 0, 3, tzinfo=timezone.utc)
    events = list(iter_events(tiny_corpus, after=after))
    # Skipping the four org bootstrap events; only github + slack left.
    kinds = [(e["provider"], e["kind"]) for _, e in events]
    assert kinds == [("github", "repo.create"), ("slack", "message")]


def test_count_events(tiny_corpus):
    counts = count_events(tiny_corpus)
    assert counts["total"] == 6
    assert counts["org"] == 4
    assert counts["github"] == 1
    assert counts["slack"] == 1


def test_validate_rejects_unknown_provider():
    with pytest.raises(SchemaError, match="unknown provider"):
        validate({"t": "2024-01-01T00:00:00Z", "provider": "msteams",
                  "kind": "message", "payload": {}})


def test_validate_rejects_unknown_kind():
    with pytest.raises(SchemaError, match="unknown kind"):
        validate({"t": "2024-01-01T00:00:00Z", "provider": "slack",
                  "kind": "wiggle", "payload": {}})


def test_validate_rejects_bad_timestamp():
    with pytest.raises(SchemaError, match="bad timestamp"):
        validate({"t": "yesterday", "provider": "slack",
                  "kind": "message", "payload": {}})


def test_validate_rejects_missing_payload():
    with pytest.raises(SchemaError, match="missing 'payload'"):
        validate({"t": "2024-01-01T00:00:00Z", "provider": "slack", "kind": "message"})
