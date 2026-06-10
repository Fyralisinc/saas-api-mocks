"""Fixtures for the Grafana mock fidelity suite (annotations API + Alerting webhook).

Seeds a deterministic instance (org) plus a small annotation stream with
staggered epoch-ms timestamps: two user/deploy annotations, one manual note, and
one alert incident (a firing + a resolved auto-annotation sharing an alertId).
Wires the Grafana ``state`` singleton + an ASGI client.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

SA_TOKEN = "glsa_grafanaMockFidelity_abcdef12"
WEBHOOK_SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
INSTANCE_HOST = "alpenlabs.grafana.net"
# 2026-01-10T09:00:00Z in epoch ms
_T0_MS = 1768035600000


# (annotation_id, offset_ms, text, tags, user_id, login, alert_id, alert_name, new_state, prev_state, dash_uid, panel_id)
ANNOS = [
    (1, 0, "Deployed strata-node v1.4.2 to production", ["deploy", "production", "strata-node"],
     5, "alice", None, "", "", "", "aGz3kPq7x", 4),
    (2, 3_600_000, "Investigating elevated p99 latency", ["incident", "note"],
     6, "bob", None, "", "", "", None, None),
    (3, 7_200_000, "HighErrorRate: 5xx error rate above 5% for 5m", ["alertname:HighErrorRate", "critical"],
     0, "", 1042, "HighErrorRate", "Alerting", "Normal", "cQ9rNv5ka", 7),
    (4, 9_000_000, "HighErrorRate: 5xx error rate above 5% for 5m", ["alertname:HighErrorRate", "critical"],
     0, "", 1042, "HighErrorRate", "Normal", "Alerting", "cQ9rNv5ka", 7),
    (5, 10_800_000, "Deployed zkaleido v2.0.1 to staging", ["deploy", "staging", "zkaleido"],
     5, "alice", None, "", "", "", None, None),
]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def grafana_run(pool) -> UUID:
    run_id = uuid4()
    await pool.execute(
        """INSERT INTO org.runs (id, size, runtime, seed, fyralis_tenant_id,
               fyralis_base_url, virtual_now, mode, speed_multiplier)
           VALUES ($1,'small','few_months',9,$2,'http://localhost:8000',now(),'frozen',1.0)""",
        run_id, uuid4())
    inst_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_grafana.instances
            (id, run_id, base_url, instance_host, org_id, org_name, sa_token,
             webhook_secret, created_at)
           VALUES ($1,$2,$3,$4,1,'Alpen Labs',$5,$6,now())""",
        inst_pk, run_id, f"https://{INSTANCE_HOST}", INSTANCE_HOST, SA_TOKEN, WEBHOOK_SECRET)
    for (aid, off, text, tags, uid, login, alert_id, aname, ns, ps, dash, panel) in ANNOS:
        tms = _T0_MS + off
        await pool.execute(
            """INSERT INTO app_grafana.annotations
                (id, instance_pk, annotation_id, time_ms, time_end_ms, text, tags,
                 dashboard_uid, panel_id, user_id, user_login, user_email, alert_id,
                 alert_name, new_state, prev_state, data, created_ms, updated_ms, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10,$11,$12,$13,$14,$15,$16,
                       '{}'::jsonb,$17,$18,$19)""",
            uuid4(), inst_pk, aid, tms, tms, text, json.dumps(tags), dash, panel,
            uid, login, f"{login}@alpenlabs.io" if login else "", alert_id, aname, ns, ps,
            tms, tms, datetime.fromtimestamp(tms / 1000, tz=timezone.utc))
    return run_id


@pytest_asyncio.fixture(loop_scope="session")
async def grafana_client(pool, grafana_run):
    from spammers.grafana import state as g_state
    from spammers.grafana.app import create_app, _FORCED_429

    g_state._STATE = g_state.GrafanaMockState(pool=pool, run_id=grafana_run)
    _FORCED_429["count"] = 0
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://mock") as c:
        yield c
    g_state._STATE = None


def auth_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {SA_TOKEN}"}


@pytest.fixture
def grafana_auth() -> dict[str, str]:
    return auth_header()
