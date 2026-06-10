"""Realistic Grafana corpus seeding.

Grafana is a NET-NEW Tier-C source: the frozen run has no Grafana corpus, so we
model realistic observability content ourselves (the brief sanctions this, like
QuickBooks projecting finance and Notion modelling pages). We synthesize the one
``app_grafana.annotations`` stream a real instance would accumulate — and that
stream, faithfully, carries BOTH:

  * **user/deploy annotations** (deploy markers, manual incident notes) — plain
    annotations authored by a person (user_id > 0, tagged ``deploy``/``incident``),
  * **alert-state-change annotations** (the historical alert timeline) — the rows
    Grafana auto-writes on every alert transition (alert_id + new_state/prev_state,
    machine-authored, user_id 0). Each incident is a firing→resolved pair.

Everything is **deterministic** off the run seed and derived from the org already
in the run (people → annotating users, GitHub repos → service names), spread
backward over a ~2-year window ending at the run's virtual-now so the backward
time-window pull pages across several pages. Idempotent: a second call after the
instance row exists is a no-op.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

import asyncpg

# Seed-stable instance identity (hand these to the ingest-client / memory).
INSTANCE_HOST = "alpenlabs.grafana.net"
BASE_URL = f"https://{INSTANCE_HOST}"
ORG_NAME = "Alpen Labs"
SA_TOKEN = "glsa_alpenLabsMockServiceAccount_5b582697"
WEBHOOK_SECRET = "9f1d3b7c2e6a48d05c91af2738e0b4a6d5c8e1f0a2b3c4d5e6f70819a2b3c4d5"

# Fallback service catalogue (used when GitHub repos aren't in the run).
_FALLBACK_SERVICES = [
    "strata-node", "strata-bridge", "strata-p2p", "zkaleido", "faucet-api",
    "checkpoint-explorer", "alpen-reth", "prover-worker",
]
# A few dashboards annotations hang off (UID like a real Grafana dashboard UID).
_DASHBOARDS = [
    ("aGz3kPq7x", "Node Overview", 4),
    ("bH7lMt2wz", "Bridge Health", 2),
    ("cQ9rNv5ka", "API Latency", 7),
    ("dW1sPx8mb", "Prover Throughput", 3),
]
_ENVS = ["production", "staging"]
_ALERTS = [
    ("HighErrorRate", {"severity": "critical", "job": "api-gateway"},
     "5xx error rate above 5% for 5m"),
    ("HighMemoryUsage", {"severity": "warning", "job": "prover-worker"},
     "container memory above 90%"),
    ("ProbeFailure", {"severity": "critical", "job": "blackbox"},
     "endpoint probe failing"),
    ("HighP99Latency", {"severity": "warning", "job": "api-gateway"},
     "p99 latency above 800ms"),
    ("DiskSpaceLow", {"severity": "warning", "job": "node-exporter"},
     "disk usage above 85%"),
    ("BridgeLagHigh", {"severity": "critical", "job": "strata-bridge"},
     "bridge operator lag above threshold"),
]


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


async def _service_names(pool: asyncpg.Pool, run_id: UUID) -> list[str]:
    try:
        rows = await pool.fetch(
            "SELECT DISTINCT r.name FROM app_github.repositories r "
            "JOIN app_github.installations i ON i.id = r.installation_pk "
            "WHERE i.run_id = $1 ORDER BY r.name", run_id)
        names = [r["name"] for r in rows if r["name"] and not r["name"].startswith(".")]
    except asyncpg.PostgresError:
        names = []
    return names or list(_FALLBACK_SERVICES)


async def seed_grafana(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    at: Optional[datetime] = None,
) -> dict[str, int]:
    """Provision the instance + a realistic annotation stream for ``run_id``.

    Idempotent. Returns ``{"annotations": N}`` (0 if already seeded)."""
    existing = await pool.fetchval(
        "SELECT id FROM app_grafana.instances WHERE run_id = $1", run_id)
    if existing is not None:
        return {"annotations": 0}

    seed_row = await pool.fetchrow("SELECT seed, virtual_now FROM org.runs WHERE id = $1", run_id)
    if seed_row is None:
        raise LookupError(f"no run {run_id}")
    rng = random.Random(int(seed_row["seed"]) ^ 0x6772_6166)  # 'graf'
    now = at or seed_row["virtual_now"] or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    inst_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_grafana.instances
            (id, run_id, base_url, instance_host, org_id, org_name, sa_token,
             webhook_secret, created_at)
           VALUES ($1,$2,$3,$4,1,$5,$6,$7,$8)""",
        inst_pk, run_id, BASE_URL, INSTANCE_HOST, ORG_NAME, SA_TOKEN,
        WEBHOOK_SECRET, now)

    # Annotating users: map a handful of org people to small int Grafana user ids.
    people = await pool.fetch(
        "SELECT handle, full_name, email FROM org.people WHERE run_id = $1 "
        "ORDER BY handle LIMIT 12", run_id)
    users = [(i + 1, p["handle"], p["email"] or f"{p['handle']}@alpenlabs.io")
             for i, p in enumerate(people)] or [(1, "ops", "ops@alpenlabs.io")]
    services = await _service_names(pool, run_id)

    window_start = now - timedelta(days=760)
    annos: list[dict] = []

    # 1) Deploy markers — roughly twice a week per active service rotation.
    day = window_start
    while day < now - timedelta(days=2):
        if rng.random() < 0.42:  # ~3 deploy-days/week
            svc = rng.choice(services)
            env = "production" if rng.random() < 0.7 else "staging"
            ver = f"v{rng.randint(0, 3)}.{rng.randint(0, 20)}.{rng.randint(0, 9)}"
            uid, login, email = rng.choice(users)
            ts = day + timedelta(hours=rng.randint(9, 19), minutes=rng.randint(0, 59))
            dash = rng.choice(_DASHBOARDS) if rng.random() < 0.5 else None
            annos.append({
                "time": ts, "end": ts,
                "text": f"Deployed {svc} {ver} to {env}",
                "tags": ["deploy", env, svc],
                "user_id": uid, "login": login, "email": email,
                "dashboard_uid": dash[0] if dash else None,
                "panel_id": dash[2] if dash else None,
                "alert_id": None,
            })
        day += timedelta(days=1)

    # 2) Alert incidents — each is a firing then a resolved auto-annotation
    #    (the historical alert timeline that rides the annotations stream).
    day = window_start + timedelta(days=rng.randint(2, 9))
    alert_seq = 1000
    while day < now - timedelta(days=1):
        if rng.random() < 0.3:  # an incident every ~3 days on average
            name, base_labels, desc = rng.choice(_ALERTS)
            alert_seq += rng.randint(1, 7)
            fire_ts = day + timedelta(hours=rng.randint(0, 23), minutes=rng.randint(0, 59))
            dur_min = rng.choice([6, 11, 18, 27, 44, 73])
            resolve_ts = fire_ts + timedelta(minutes=dur_min)
            dash = rng.choice(_DASHBOARDS)
            for new_state, prev_state, ts in (
                ("Alerting", "Normal", fire_ts),
                ("Normal", "Alerting", resolve_ts),
            ):
                annos.append({
                    "time": ts, "end": ts,
                    "text": f"{name}: {desc}",
                    "tags": [f"alertname:{name}", base_labels.get("severity", "warning")],
                    "user_id": 0, "login": "", "email": "",
                    "dashboard_uid": dash[0], "panel_id": dash[2],
                    "alert_id": alert_seq, "alert_name": name,
                    "new_state": new_state, "prev_state": prev_state,
                })
        day += timedelta(days=1)

    # 3) Manual incident notes — occasional human annotations.
    notes = [
        "Investigating elevated p99 latency on api-gateway",
        "Rolled back prover-worker after OOM spike",
        "Postmortem published for the bridge lag incident",
        "Scaled up reth nodes ahead of mainnet checkpoint",
        "Silenced DiskSpaceLow during planned maintenance",
        "Mitigated 5xx spike by draining a bad pod",
    ]
    day = window_start + timedelta(days=rng.randint(10, 25))
    while day < now - timedelta(days=2):
        if rng.random() < 0.18:
            uid, login, email = rng.choice(users)
            ts = day + timedelta(hours=rng.randint(8, 20), minutes=rng.randint(0, 59))
            annos.append({
                "time": ts, "end": ts,
                "text": rng.choice(notes),
                "tags": ["incident", "note"],
                "user_id": uid, "login": login, "email": email,
                "dashboard_uid": None, "panel_id": None,
                "alert_id": None,
            })
        day += timedelta(days=18)

    # Assign Grafana integer ids in chronological order (id grows with time).
    annos.sort(key=lambda a: a["time"])
    for n, a in enumerate(annos, start=1):
        tms, ems = _ms(a["time"]), _ms(a["end"])
        await pool.execute(
            """INSERT INTO app_grafana.annotations
                (id, instance_pk, annotation_id, time_ms, time_end_ms, text, tags,
                 dashboard_uid, panel_id, user_id, user_login, user_email, alert_id,
                 alert_name, new_state, prev_state, data, created_ms, updated_ms,
                 created_at, is_historical)
               VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10,$11,$12,$13,$14,$15,$16,
                       '{}'::jsonb,$17,$18,$19,TRUE)""",
            uuid4(), inst_pk, n, tms, ems, a["text"], json.dumps(a["tags"]),
            a.get("dashboard_uid"), a.get("panel_id"), a.get("user_id", 0),
            a.get("login", ""), a.get("email", ""), a.get("alert_id"),
            a.get("alert_name", ""), a.get("new_state", ""), a.get("prev_state", ""),
            tms, ems, a["time"].astimezone(timezone.utc))

    return {"annotations": len(annos)}
