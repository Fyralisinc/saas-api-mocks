"""Outbound Grafana Alerting webhook delivery.

When the orchestrator drains a live ``grafana.alert`` timeline event, this builds
the **Alertmanager-superset alert group** Grafana's webhook contact point POSTs
when an alert fires/resolves:

  {"receiver":…, "status":"firing", "alerts":[{…}], "groupKey":…,
   "commonLabels":{…}, "externalURL":"https://<host>/", "version":"1", …}

and signs it ``X-Grafana-Alerting-Signature: <hex(HMAC-SHA256(secret, rawBody))>``
— a **bare lowercase hex** digest with NO ``sha256=`` prefix (Grafana 12.0+), over
the raw body alone (no timestamp envelope by default). The ``externalURL`` host
is the instance host, which is how the consumer tenant-resolves the delivery.

The mock's webhook secret is the instance row's ``webhook_secret`` — hand it to
the consumer as its ``GRAFANA_WEBHOOK_SECRET``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Mapping
from uuid import UUID

import asyncpg
import structlog

from spammers.common.signing import grafana_sign
from spammers.common.webhook_emitter import deliver, mark_emitted

log = structlog.get_logger("spammers.grafana.webhooks")

# Grafana's zero-value sentinel for a still-firing alert's endsAt.
_ZERO_TIME = "0001-01-01T00:00:00Z"


def _rfc3339(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_alert_group(payload: dict, *, external_url: str) -> dict:
    """Assemble the Alertmanager-superset alert-group webhook body from a thin
    ``grafana.alert`` timeline payload."""
    status = payload.get("status", "firing")
    alertname = payload.get("alertname", "Alert")
    labels = dict(payload.get("labels") or {})
    labels.setdefault("alertname", alertname)
    annotations = dict(payload.get("annotations") or {})
    starts_at = payload.get("starts_at") or _rfc3339(datetime.now(timezone.utc))
    ends_at = payload.get("ends_at") or (_ZERO_TIME if status == "firing"
                                         else _rfc3339(datetime.now(timezone.utc)))
    fingerprint = payload.get("fingerprint", "")
    group_key = payload.get("group_key", "{}/{}:{}")
    values = payload.get("values") or {}

    alert = {
        "status": status,
        "labels": labels,
        "annotations": annotations,
        "startsAt": starts_at,
        "endsAt": ends_at,
        "generatorURL": payload.get("generator_url", ""),
        "fingerprint": fingerprint,
        "silenceURL": "",
        "dashboardURL": payload.get("dashboard_url", ""),
        "panelURL": payload.get("panel_url", ""),
        "values": values,
    }
    n = 1
    common_labels = {k: v for k, v in labels.items()}
    title = (f"[{'FIRING' if status == 'firing' else 'RESOLVED'}:{n}]  "
             f"{alertname} ({labels.get('service', '')})").rstrip()
    return {
        "receiver": payload.get("receiver", "fyralis"),
        "status": status,
        "orgId": payload.get("org_id", 1),
        "alerts": [alert],
        "groupLabels": {"alertname": alertname},
        "commonLabels": common_labels,
        "commonAnnotations": annotations,
        "externalURL": external_url,
        "version": "1",
        "groupKey": group_key,
        "truncatedAlerts": 0,
        "title": title,
        "state": "alerting" if status == "firing" else "ok",
        "message": annotations.get("summary") or annotations.get("description") or title,
    }


async def emit_event(
    pool: asyncpg.Pool,
    *,
    run_id: UUID,
    event_id: UUID,
    grafana_webhook_url: str,
) -> tuple[int, str]:
    """Fetch a ``grafana.alert`` timeline event and POST its signed alert group."""
    ev = await pool.fetchrow(
        "SELECT payload FROM timeline.events WHERE id = $1", event_id)
    if ev is None:
        raise LookupError(f"grafana timeline event not found: {event_id}")
    payload = ev["payload"] if isinstance(ev["payload"], dict) else json.loads(ev["payload"])

    inst = await pool.fetchrow(
        "SELECT instance_host, org_id, webhook_secret FROM app_grafana.instances "
        "WHERE run_id = $1", run_id)
    if inst is None:
        raise LookupError(f"no grafana instance for run {run_id}")
    external_url = f"https://{inst['instance_host']}/"
    payload.setdefault("org_id", inst["org_id"])
    envelope = build_alert_group(payload, external_url=external_url)
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    secret = inst["webhook_secret"]

    def sign(b: bytes) -> Mapping[str, str]:
        return {"X-Grafana-Alerting-Signature": grafana_sign(secret, b)}

    status, text = await deliver(url=grafana_webhook_url, body=body, sign=sign)
    await mark_emitted(pool, event_id, status=status, attempt_at=datetime.now(timezone.utc))
    log.info("grafana_event_emitted", event_id=str(event_id),
             alertname=payload.get("alertname"), status=status)
    return status, text
