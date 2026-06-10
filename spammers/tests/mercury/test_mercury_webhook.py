"""Mercury transaction-webhook fidelity — Stripe-style HMAC signing + the
JSON-merge-patch event envelope. Pure (non-DB) assertions on the wire contract.

Audited vs docs.mercury.com/reference/webhooks:
  * Mercury-Signature = ``t=<unix_seconds>,v1=<hex>`` where the hex is
    HMAC-SHA256 over ``"{t}.{rawBody}"`` (bare hex, NO sha256= prefix, NOT base64).
  * the event is a merge-patch envelope: id/resourceType/resourceId/operationType/
    resourceVersion/occurredAt/changedPaths/mergePatch/previousValues; occurredAt
    uses MICROSECOND precision (distinct from the REST bodies' seconds precision).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from spammers.common.signing import mercury_sign, mercury_verify
from spammers.mercury.webhooks import build_event


def test_mercury_signature_is_t_v1_hex():
    secret = "whsec_test"
    body = b'{"resourceType":"transaction"}'
    sig = mercury_sign(secret, body, 1735689600)
    assert sig.startswith("t=1735689600,v1="), "Stripe-style t=,v1= pair"
    v1 = sig.split("v1=", 1)[1]
    assert len(v1) == 64 and all(c in "0123456789abcdef" for c in v1), "bare lowercase hex"
    assert "sha256=" not in sig
    assert mercury_verify(secret, sig, body)
    # tampered body / wrong secret / tampered timestamp must all fail
    assert not mercury_verify(secret, sig, body + b"x")
    assert not mercury_verify("wrong", sig, body)
    assert not mercury_verify(secret, "t=1735689601,v1=" + v1, body)


def test_signature_binds_the_timestamp():
    secret, body = "whsec_test", b"{}"
    a = mercury_sign(secret, body, 100)
    b = mercury_sign(secret, body, 200)
    assert a.split("v1=")[1] != b.split("v1=")[1], "the timestamp is part of the signed string"


def test_build_event_envelope_shape():
    payload = {
        "txn_id": "aaaa0001-0000-4000-8000-000000000001",
        "operation": "create",
        "resource_version": 1,
        "merge_patch": {
            "id": "aaaa0001-0000-4000-8000-000000000001",
            "accountId": "11111111-1111-4111-8111-111111111111",
            "amount": -1200.0, "status": "pending", "kind": "externalTransfer",
        },
        "previous_values": {},
    }
    ev = build_event(payload, event_id=UUID("dddd0001-0000-4000-8000-000000000001"),
                     occurred_at=datetime(2026, 1, 31, 12, 0, 0, 123456, tzinfo=timezone.utc))
    for k in ("id", "resourceType", "resourceId", "operationType", "resourceVersion",
              "occurredAt", "changedPaths", "mergePatch", "previousValues"):
        assert k in ev, f"event missing {k}"
    assert ev["resourceType"] == "transaction"
    assert ev["resourceId"] == "aaaa0001-0000-4000-8000-000000000001"
    assert ev["operationType"] == "create"
    assert ev["resourceVersion"] == 1
    # occurredAt is microsecond precision (a '.' before the Z) — NOT the REST seconds form
    assert ev["occurredAt"] == "2026-01-31T12:00:00.123456Z"
    assert ev["mergePatch"]["accountId"] == "11111111-1111-4111-8111-111111111111"


def test_build_event_update_preserves_changed_paths_and_previous():
    payload = {
        "txn_id": "aaaa0001-0000-4000-8000-000000000001",
        "operation": "update",
        "resource_version": 2,
        "changed_paths": ["status", "postedAt"],
        "merge_patch": {"status": "sent", "postedAt": "2026-02-01T00:00:00Z"},
        "previous_values": {"status": "pending", "postedAt": None},
    }
    ev = build_event(payload, event_id=UUID("dddd0002-0000-4000-8000-000000000002"),
                     occurred_at=datetime(2026, 2, 1, tzinfo=timezone.utc))
    assert ev["operationType"] == "update"
    assert ev["resourceVersion"] == 2
    assert ev["changedPaths"] == ["status", "postedAt"]
    assert ev["previousValues"] == {"status": "pending", "postedAt": None}
