"""Grafana Alerting-webhook fidelity — HMAC signing + the Alertmanager-superset
alert-group body. These are pure (non-DB) assertions on the wire contract.

Audited vs grafana.com/docs (webhook contact point, Grafana 12.0+ HMAC):
  * X-Grafana-Alerting-Signature = bare lowercase hex HMAC-SHA256 over the raw
    body alone (NO sha256= prefix); optional `{ts}:`+body mode when a timestamp
    header is configured.
  * the alert group is Alertmanager-superset with Grafana extensions
    (title/state/message); a still-firing alert's endsAt is the zero sentinel.
"""
from __future__ import annotations

from spammers.common.signing import grafana_sign, grafana_verify
from spammers.grafana.webhooks import build_alert_group, _ZERO_TIME


def test_grafana_signature_is_bare_hex_no_prefix():
    secret = "s3cr3t"
    body = b'{"status":"firing"}'
    sig = grafana_sign(secret, body)
    assert "=" not in sig, "X-Grafana-Alerting-Signature is a BARE hex digest (no sha256= prefix)"
    assert len(sig) == 64 and all(c in "0123456789abcdef" for c in sig)
    assert grafana_verify(secret, sig, body)
    assert not grafana_verify(secret, sig, body + b"x"), "tampered body must fail verification"
    assert not grafana_verify("wrong", sig, body)


def test_grafana_timestamp_mode_changes_signature():
    secret, body = "s3cr3t", b"{}"
    assert grafana_sign(secret, body) != grafana_sign(secret, body, timestamp=123)
    assert grafana_verify(secret, grafana_sign(secret, body, timestamp=123), body, timestamp=123)


def test_alert_group_envelope_shape():
    payload = {
        "status": "firing",
        "alertname": "HighErrorRate",
        "labels": {"severity": "critical", "service": "api-gateway"},
        "annotations": {"summary": "5xx spike"},
        "starts_at": "2026-01-10T09:00:00Z",
        "ends_at": None,
        "group_key": "{}/{alertname=\"HighErrorRate\"}:{}",
        "fingerprint": "c6eadffa33fcdf37",
    }
    env = build_alert_group(payload, external_url="https://alpenlabs.grafana.net/")
    for k in ("receiver", "status", "alerts", "groupLabels", "commonLabels",
              "commonAnnotations", "externalURL", "version", "groupKey",
              "truncatedAlerts", "title", "state", "message"):
        assert k in env, f"alert group missing {k}"
    assert env["status"] == "firing"
    assert env["externalURL"] == "https://alpenlabs.grafana.net/"
    alert = env["alerts"][0]
    assert alert["status"] == "firing"
    assert alert["endsAt"] == _ZERO_TIME, "a still-firing alert's endsAt is the zero sentinel"
    assert alert["labels"]["alertname"] == "HighErrorRate"
    assert "startsAt" in alert and "fingerprint" in alert


def test_alert_group_resolved_has_real_endsat():
    payload = {"status": "resolved", "alertname": "HighErrorRate",
               "labels": {}, "annotations": {},
               "starts_at": "2026-01-10T09:00:00Z", "ends_at": "2026-01-10T09:30:00Z"}
    env = build_alert_group(payload, external_url="https://x.grafana.net/")
    assert env["status"] == "resolved"
    assert env["state"] == "ok"
    assert env["alerts"][0]["endsAt"] == "2026-01-10T09:30:00Z"
