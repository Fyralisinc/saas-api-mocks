"""Grafana annotation + org JSON shapes.

``GET /api/annotations`` returns a **bare JSON array** of annotation objects.
The authoritative shape is Grafana's ``ItemDTO`` (pkg/services/annotations),
where **every field is ``omitempty``** — the single biggest byte-faithfulness
gotcha. So:

  * a manual / deploy annotation (``alert_id`` NULL, ``user_id`` > 0) carries
    ``id, time, timeEnd, text, tags, userId, login, email`` (+ dashboardUID/panelId
    when dashboard-scoped) and **omits** the alert fields entirely;
  * an alert-state-change annotation (``alert_id`` set, machine ``user_id`` 0)
    carries ``id, time, timeEnd, text, tags, alertId, alertName, newState,
    prevState`` and **omits** ``userId/login/email``.

A zero ``alertId`` is NOT serialized as ``"alertId":0`` — the key is dropped. The
legacy HTTP-API doc example (which shows ``"alertId":0,"newState":""``) predates
the ``omitempty`` DTO; a modern (v10+/v12) instance omits them.

Annotation timestamps (``time``/``timeEnd``/``created``/``updated``) are **epoch
milliseconds** (int) — distinct from the RFC3339 strings the Alerting webhook
uses for ``startsAt``/``endsAt``.
"""
from __future__ import annotations

from typing import Any


def annotation_dto(row: dict) -> dict[str, Any]:
    """Project an ``app_grafana.annotations`` row into Grafana's ItemDTO shape.

    Emits only non-empty fields (``omitempty`` semantics) so the wire bytes match
    a modern Grafana instance.
    """
    out: dict[str, Any] = {
        "id": int(row["annotation_id"]),
        "time": int(row["time_ms"]),
        "timeEnd": int(row["time_end_ms"]),
        "text": row.get("text") or "",
    }

    alert_id = row.get("alert_id")
    if alert_id:
        # Alert-state-change annotation (machine-authored).
        out["alertId"] = int(alert_id)
        if row.get("alert_name"):
            out["alertName"] = row["alert_name"]
        if row.get("new_state"):
            out["newState"] = row["new_state"]
        if row.get("prev_state"):
            out["prevState"] = row["prev_state"]
    else:
        # User-authored annotation (deploy marker / manual note / region).
        uid = int(row.get("user_id") or 0)
        if uid:
            out["userId"] = uid
        if row.get("user_login"):
            out["login"] = row["user_login"]
        if row.get("user_email"):
            out["email"] = row["user_email"]

    if row.get("dashboard_uid"):
        out["dashboardUID"] = row["dashboard_uid"]
    if row.get("panel_id"):
        out["panelId"] = int(row["panel_id"])

    tags = row.get("tags")
    if isinstance(tags, str):
        import json as _json
        tags = _json.loads(tags)
    if tags:
        out["tags"] = list(tags)

    created = row.get("created_ms")
    if created:
        out["created"] = int(created)
    updated = row.get("updated_ms")
    if updated:
        out["updated"] = int(updated)

    data = row.get("data")
    if isinstance(data, str):
        import json as _json
        data = _json.loads(data)
    if data:
        out["data"] = data
    return out


def org_dto(row: dict) -> dict[str, Any]:
    """``GET /api/org`` — the current org: ``{"id":1,"name":"Main Org."}``.

    The current-org endpoint returns only id + name (the ``address`` sub-object
    is exposed on the admin ``/api/orgs/:id`` endpoints, not here).
    """
    return {"id": int(row["org_id"]), "name": row["org_name"]}
