-- =============================================================================
-- app_grafana.*  — Grafana observability projection state
--
-- One Grafana instance (org) per run, identified by its instance host like the
-- real service (e.g. alpenlabs.grafana.net). A single org-scoped service-account
-- Bearer token reads the whole org's annotations; the same instance row also
-- carries the HMAC webhook secret that signs the Alerting webhook.
--
-- Grafana is a TWO-CHANNEL source. The historical pull surface is
-- ``GET /api/annotations`` — which carries BOTH user/deploy annotations AND the
-- auto-created alert-state-change annotations (tagged alertId/newState/prevState).
-- The live push surface is the Alerting webhook (an Alertmanager-superset alert
-- group), an independent stream. Annotations are the only thing persisted here;
-- the live alert group is built at inject time and rides a timeline event.
--
-- Annotation timestamps are EPOCH MILLISECONDS (Grafana's native unit), stored
-- as BIGINT; ``created_at`` is the wall-clock the mock uses for ordering/replay.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS app_grafana;

CREATE TABLE IF NOT EXISTS app_grafana.instances (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    base_url TEXT NOT NULL,                 -- https://<instance>.grafana.net
    instance_host TEXT NOT NULL,            -- <instance>.grafana.net (joins backfill<->live)
    org_id BIGINT NOT NULL DEFAULT 1,
    org_name TEXT NOT NULL DEFAULT 'Main Org.',
    sa_token TEXT NOT NULL,                 -- service-account Bearer token (glsa_…)
    webhook_secret TEXT NOT NULL,           -- HMAC-SHA256 alerting-webhook secret
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (run_id)
);

-- One annotation per row. A plain annotation (manual note / deploy marker /
-- region) has alert_id NULL and a user_id>0; an alert-state-change annotation has
-- a non-null alert_id + new_state/prev_state and user_id 0 (machine). The handler
-- splits the two on alert_id/new_state. Org-wide annotations have NULL
-- dashboard_uid/panel_id. ``annotation_id`` is Grafana's integer resource id.
CREATE TABLE IF NOT EXISTS app_grafana.annotations (
    id UUID PRIMARY KEY,
    instance_pk UUID NOT NULL REFERENCES app_grafana.instances(id) ON DELETE CASCADE,
    annotation_id BIGINT NOT NULL,          -- Grafana's int `id`
    time_ms BIGINT NOT NULL,                -- epoch ms — annotation start `time`
    time_end_ms BIGINT NOT NULL,            -- epoch ms — `timeEnd` (==time for a point)
    text TEXT NOT NULL DEFAULT '',
    tags JSONB NOT NULL DEFAULT '[]'::jsonb, -- array of strings
    dashboard_uid TEXT,                     -- NULL for org-wide annotations
    panel_id BIGINT,                        -- NULL for org-wide annotations
    user_id BIGINT NOT NULL DEFAULT 0,      -- 0 == machine (alert annotations)
    user_login TEXT NOT NULL DEFAULT '',
    user_email TEXT NOT NULL DEFAULT '',
    alert_id BIGINT,                        -- NULL == plain annotation
    alert_name TEXT NOT NULL DEFAULT '',
    new_state TEXT NOT NULL DEFAULT '',
    prev_state TEXT NOT NULL DEFAULT '',
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_ms BIGINT NOT NULL,             -- epoch ms — row created
    updated_ms BIGINT NOT NULL,             -- epoch ms — row updated
    created_at TIMESTAMPTZ NOT NULL,
    is_historical BOOLEAN NOT NULL DEFAULT TRUE,
    timeline_event_id UUID,
    UNIQUE (instance_pk, annotation_id)
);
-- Reads are a backward time-window walk: newest-first by (timeEnd, time) within a
-- [from, to] epoch-ms window. Index that ordering.
CREATE INDEX IF NOT EXISTS grafana_annotations_instance_time_idx
    ON app_grafana.annotations(instance_pk, time_end_ms DESC, time_ms DESC);
