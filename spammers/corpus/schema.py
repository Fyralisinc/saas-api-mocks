"""Event schema for ``events.jsonl``.

Each line of the file is a JSON object with this shape:

    {"t": "2024-09-14T15:23:00Z",
     "provider": "github",
     "kind": "pr.open",
     "actor": "person:alice",
     "payload": {"repo": "repo:strata-bridge", "number": 42, ...}}

``provider`` is one of the 8 mocked SaaS providers plus a special ``org``
namespace for bootstrap events (creating people/teams/projects before any
provider event references them).

``kind`` is a dotted noun.verb string scoped to the provider. We keep the
allow-list explicit so a typo in the corpus file fails loudly at load time
instead of silently producing no DB rows.

The schema is **versioned**: bump ``SCHEMA_VERSION`` whenever the on-disk
shape changes, and require the corpus header to match.
"""
from __future__ import annotations

from datetime import datetime
from typing import TypedDict, NotRequired

SCHEMA_VERSION = "v1"


class Event(TypedDict):
    t: str                       # RFC3339 / ISO8601, always UTC
    provider: str
    kind: str
    actor: NotRequired[str | None]   # corpus_id of the acting person, or None for system events
    payload: dict


# Allow-list of (provider, kind) pairs the replayer knows about. The renderer
# in alpen-corpus and the dispatcher in spammers/corpus/replay.py both gate on
# this set — keeping it as the single source of truth means schema drift is
# caught by either side.
KINDS: dict[str, set[str]] = {
    "org": {
        "person.create", "person.depart", "person.role_change",
        "team.create", "team.dissolve",
        "project.create", "project.close", "project.milestone",
    },
    "slack": {
        "user.create", "channel.create", "channel.archive", "message",
    },
    "discord": {
        "user.create", "channel.create", "message",
    },
    "github": {
        "repo.create", "user.create",
        "commit", "pr.open", "pr.close", "pr.merge", "review.submit",
        "issue.open", "issue.close", "issue.comment",
        "release.publish",
    },
    "jira": {
        "user.create", "project.create",
        "issue.create", "issue.transition", "issue.assign", "comment",
    },
    "notion": {
        "workspace.init", "database.create", "page.create", "page.update",
    },
    "gmail": {
        "message",
    },
    "calendar": {
        "event.create", "event.update", "event.cancel",
    },
    "drive": {
        "file.create", "file.update", "file.trash", "comment", "revision",
    },
}


class SchemaError(ValueError):
    """Raised when a line in events.jsonl violates the schema."""


def validate(event: dict, *, line_no: int | None = None) -> Event:
    """Validate one event dict; return the typed Event or raise SchemaError."""
    where = f" (line {line_no})" if line_no is not None else ""
    for key in ("t", "provider", "kind", "payload"):
        if key not in event:
            raise SchemaError(f"missing '{key}'{where}")
    try:
        datetime.fromisoformat(event["t"].replace("Z", "+00:00"))
    except (TypeError, ValueError) as e:
        raise SchemaError(f"bad timestamp {event['t']!r}{where}: {e}")
    if event["provider"] not in KINDS:
        raise SchemaError(f"unknown provider {event['provider']!r}{where}")
    if event["kind"] not in KINDS[event["provider"]]:
        raise SchemaError(
            f"unknown kind {event['provider']}.{event['kind']!r}{where}; "
            f"allowed: {sorted(KINDS[event['provider']])}"
        )
    if not isinstance(event["payload"], dict):
        raise SchemaError(f"payload must be object{where}")
    return event  # type: ignore[return-value]
