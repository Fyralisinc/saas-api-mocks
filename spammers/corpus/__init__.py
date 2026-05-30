"""Corpus replay — phase 5 of the Alpen Labs model-layer testing pipeline.

The sibling ``alpen-corpus`` repo produces a frozen ``events.jsonl`` file
(one timed event per line) that captures a real company's ~4 year history.
This package ingests that file into the same provider DBs the profile-driven
generator targets, advancing a per-run ``replay_cursor`` as wall-clock ticks.

Entry points:
    - ``corpus.loader.iter_events(path)``   — stream events in time order
    - ``corpus.replay.backfill(...)``       — land events up to a cursor
    - ``corpus.cursor.advance(...)``        — move the cursor (called by daemon)
"""
from spammers.corpus.schema import Event, validate
from spammers.corpus.loader import iter_events, count_events

__all__ = ["Event", "validate", "iter_events", "count_events"]
