"""Grafana mock — FastAPI app.

The surface a connector hits for ingestion is the **annotations** endpoint plus
an org probe:

    GET /api/annotations?from=&to=&limit=&type=&tags=…   (epoch-ms window, newest-first)
    GET /api/org                                          (connectivity + credential probe)

Auth is an org-scoped service-account ``Authorization: Bearer glsa_…``. The
annotations response is a **bare JSON array** (not an object); ``from``/``to`` are
**epoch milliseconds**; there is no opaque cursor or ``Link`` header — pagination
is a backward time-window walk (a page shorter than ``limit`` is the last page).

The single annotation stream carries BOTH user/deploy annotations and the
auto-created alert-state-change annotations (alertId/newState/prevState). The live
alert webhook is a separate channel (see webhooks.py).

Mock-only: ``POST /_control/rate_limit?count=N`` arms N forced 429s (mirrors
notion/qbo). NOTE: Grafana's *core* HTTP API does not document a 429/Retry-After
contract (only Loki/Cloud-gateway does); the forced-429 path is a mock-only knob
to exercise a consumer's retry budget and is not on the validated read path.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from spammers.grafana import dto as _dto
from spammers.grafana import state as _state
from spammers.grafana.auth import is_authed

_FORCED_429 = {"count": 0}


@asynccontextmanager
async def _lifespan(app: FastAPI):
    await _state.startup()
    yield
    await _state.shutdown()


def _error(status: int, message: str) -> JSONResponse:
    # Grafana's core error envelope; only `message` is guaranteed across handlers.
    return JSONResponse({"message": message, "traceID": ""}, status_code=status)


def _unauthorized() -> JSONResponse:
    return _error(401, "Unauthorized")


def _int_param(qp, name: str) -> Optional[int]:
    raw = qp.get(name)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def create_app() -> FastAPI:
    app = FastAPI(title="Grafana mock", lifespan=_lifespan)

    @app.middleware("http")
    async def _rate_limit(request: Request, call_next):
        if request.url.path.startswith("/api/") and _FORCED_429["count"] > 0:
            _FORCED_429["count"] -= 1
            resp = _error(429, "Too Many Requests")
            # Grafana Cloud's gateway emits Retry-After on 429; the core API does
            # not document it. Mock-only path — present so a consumer's retry
            # budget is exercised.
            resp.headers["Retry-After"] = "1"
            return resp
        return await call_next(request)

    @app.get("/_health")
    async def health():
        s = _state.state()
        inst = await _state.instance_for_run(s.pool, s.run_id)
        return {"ok": True, "service": "grafana-mock",
                "run_id": str(s.run_id),
                "instance_host": inst["instance_host"] if inst else None}

    @app.post("/_control/rate_limit")
    async def arm_rate_limit(count: int = 1):
        _FORCED_429["count"] = max(0, count)
        return {"armed": _FORCED_429["count"]}

    @app.get("/api/org")
    async def org(request: Request):
        if not is_authed(request):
            return _unauthorized()
        s = _state.state()
        inst = await _state.instance_for_run(s.pool, s.run_id)
        if inst is None:
            return _error(404, "Organization not found")
        return JSONResponse(_dto.org_dto(dict(inst)))

    @app.get("/api/annotations")
    async def annotations(request: Request):
        if not is_authed(request):
            return _unauthorized()
        s = _state.state()
        inst = await _state.instance_for_run(s.pool, s.run_id)
        if inst is None:
            # No instance for this run -> an empty org reads as an empty list.
            return JSONResponse([])

        qp = request.query_params
        frm = _int_param(qp, "from")
        to = _int_param(qp, "to")
        limit = _int_param(qp, "limit")
        if limit is None or limit < 1:
            limit = 100  # Grafana's documented default
        atype = (qp.get("type") or "").strip().lower()
        alert_id = _int_param(qp, "alertId")
        panel_id = _int_param(qp, "panelId")
        user_id = _int_param(qp, "userId")
        dash_uid = qp.get("dashboardUID")
        tags = qp.getlist("tags")

        clauses = ["instance_pk = $1"]
        params: list[Any] = [inst["id"]]
        # Window is keyed on `time` (epoch ms) — the value the backward walk pages
        # on (next page upper bound = min(time)-1ms).
        if frm is not None:
            params.append(frm); clauses.append(f"time_ms >= ${len(params)}")
        if to is not None:
            params.append(to); clauses.append(f"time_ms <= ${len(params)}")
        if atype == "alert":
            clauses.append("alert_id IS NOT NULL")
        elif atype == "annotation":
            clauses.append("alert_id IS NULL")
        if alert_id is not None:
            params.append(alert_id); clauses.append(f"alert_id = ${len(params)}")
        if panel_id is not None:
            params.append(panel_id); clauses.append(f"panel_id = ${len(params)}")
        if user_id is not None:
            params.append(user_id); clauses.append(f"user_id = ${len(params)}")
        if dash_uid:
            params.append(dash_uid); clauses.append(f"dashboard_uid = ${len(params)}")
        for t in tags:  # repeated `tags=` are AND-filtered
            params.append(t); clauses.append(f"tags ? ${len(params)}")

        params.append(limit)
        sql = (
            "SELECT annotation_id, time_ms, time_end_ms, text, tags, dashboard_uid, "
            "panel_id, user_id, user_login, user_email, alert_id, alert_name, "
            "new_state, prev_state, data, created_ms, updated_ms "
            "FROM app_grafana.annotations WHERE " + " AND ".join(clauses) +
            # Newest-first by (timeEnd, time) — Grafana's store ordering.
            f" ORDER BY time_end_ms DESC, time_ms DESC, annotation_id DESC LIMIT ${len(params)}"
        )
        rows = await s.pool.fetch(sql, *params)
        return JSONResponse([_dto.annotation_dto(dict(r)) for r in rows])

    return app


app = create_app()
