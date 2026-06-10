"""Hard-fail tests for Figma's webhook (the body-PASSCODE scheme — NO HMAC).

Figma Webhooks v2 authenticate with a **plaintext ``passcode`` carried as a
top-level JSON field in the delivery body** — there is NO signature header and NO
HMAC (developers.figma.com/docs/rest-api/webhooks-security). Every delivery shares
``{event_type, passcode, timestamp, webhook_id}``; ``FILE_VERSION_UPDATE`` adds
``version_id``/``description`` (NO ``label`` — per the OpenAPI spec); ``FILE_COMMENT``
adds a ``comment`` fragment array + ``comment_id``; ``PING`` is base-only.
"""
from __future__ import annotations

from datetime import datetime, timezone

from spammers.figma.webhooks import build_event
from spammers.figma.dto import WEBHOOK_EVENT_TYPES

PASSCODE = "fgwh_test_passcode_123"
WEBHOOK_ID = "777001"
OCCURRED = datetime(2026, 2, 23, 20, 27, 16, tzinfo=timezone.utc)


def test_file_version_update_envelope_carries_passcode_no_label():
    payload = {"event_type": "FILE_VERSION_UPDATE", "event": {
        "created_at": "2026-02-23T20:27:16Z",
        "description": "Live update for Onboarding Flow.",
        "file_key": "FKEYaaaa", "file_name": "Onboarding Flow",
        "triggered_by": {"id": "111", "handle": "Ada", "img_url": "https://img/ada"},
        "version_id": "1100000000099"}}
    ev = build_event(payload, passcode=PASSCODE, webhook_id=WEBHOOK_ID, occurred=OCCURRED)
    # The passcode IS the auth — it rides in the body, not a header.
    assert ev["passcode"] == PASSCODE
    assert ev["event_type"] == "FILE_VERSION_UPDATE"
    assert ev["event_type"] in WEBHOOK_EVENT_TYPES
    assert ev["webhook_id"] == WEBHOOK_ID
    # timestamp is UTC ISO-8601 with Z.
    assert ev["timestamp"] == "2026-02-23T20:27:16Z"
    assert ev["version_id"] == "1100000000099"
    assert ev["file_key"] == "FKEYaaaa"
    # triggered_by is a FULL User {id, handle, img_url}.
    assert set(ev["triggered_by"]) == {"id", "handle", "img_url"}
    # NO `label` field on FILE_VERSION_UPDATE (the OpenAPI spec omits it).
    assert "label" not in ev


def test_file_comment_envelope_fragment_array():
    payload = {"event_type": "FILE_COMMENT", "event": {
        "comment": [{"text": "Live review comment"}],
        "comment_id": "9000000001",
        "created_at": "2026-02-23T20:27:16Z",
        "file_key": "FKEYaaaa", "file_name": "Onboarding Flow",
        "mentions": [],
        "triggered_by": {"id": "222", "handle": "Alan", "img_url": "https://img/alan"}}}
    ev = build_event(payload, passcode=PASSCODE, webhook_id=WEBHOOK_ID, occurred=OCCURRED)
    assert ev["event_type"] == "FILE_COMMENT"
    assert ev["passcode"] == PASSCODE
    assert ev["comment_id"] == "9000000001"
    # `comment` is an array of CommentFragment {text} | {mention}.
    assert ev["comment"] == [{"text": "Live review comment"}]


def test_ping_is_base_envelope_only():
    ev = build_event({"event_type": "PING"}, passcode=PASSCODE, webhook_id=WEBHOOK_ID,
                     occurred=OCCURRED)
    # PING carries only the base envelope — no file fields, no triggered_by.
    assert set(ev) == {"event_type", "passcode", "timestamp", "webhook_id"}
    assert ev["event_type"] == "PING"
    assert ev["passcode"] == PASSCODE
