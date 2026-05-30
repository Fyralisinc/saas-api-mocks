-- =============================================================================
-- app_jira.*  — Jira Cloud REST v3 projection state
--
-- One installation per run (the Atlassian site: its own base_url, the
-- account_email + api_token used for HTTP Basic auth, cloud_id, webhook secret),
-- one project per shard, and one issue per row. Each issue carries an inline
-- changelog (``changelogs`` rows -> expand=changelog histories) and comments
-- (``comments`` rows -> fields.comment.comments). ``updated_at`` is the JQL
-- ``updated`` high-water the poll cursor + reconcile probe read against (minute
-- precision, inclusive ``>=`` — faithful to real Jira). Status/resolution
-- changelog items are the state_change signal.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS app_jira;

CREATE TABLE IF NOT EXISTS app_jira.installations (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    base_url TEXT NOT NULL,                 -- https://<site>.atlassian.net
    site_name TEXT NOT NULL,
    cloud_id TEXT NOT NULL,
    account_email TEXT NOT NULL,            -- the Basic-auth principal
    account_id TEXT NOT NULL,
    api_token TEXT NOT NULL,                -- the Basic-auth secret
    webhook_secret TEXT NOT NULL,
    UNIQUE (run_id)
);

-- A Jira user (1:1 with org.people) — carries the site-stable accountId the
-- issue/comment/changelog author objects reference.
CREATE TABLE IF NOT EXISTS app_jira.users (
    id UUID PRIMARY KEY,
    installation_pk UUID NOT NULL REFERENCES app_jira.installations(id) ON DELETE CASCADE,
    person_id UUID REFERENCES org.people(id),
    account_id TEXT NOT NULL,
    email TEXT NOT NULL,
    display_name TEXT NOT NULL,
    UNIQUE (installation_pk, account_id)
);

CREATE TABLE IF NOT EXISTS app_jira.projects (
    id UUID PRIMARY KEY,
    installation_pk UUID NOT NULL REFERENCES app_jira.installations(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL,
    key TEXT NOT NULL,
    name TEXT NOT NULL,
    project_type_key TEXT NOT NULL DEFAULT 'software',
    lead_account_id TEXT,
    UNIQUE (installation_pk, key)
);

CREATE TABLE IF NOT EXISTS app_jira.issues (
    id UUID PRIMARY KEY,
    installation_pk UUID NOT NULL REFERENCES app_jira.installations(id) ON DELETE CASCADE,
    project_pk UUID NOT NULL REFERENCES app_jira.projects(id) ON DELETE CASCADE,
    issue_id TEXT NOT NULL,                 -- numeric string (Jira's internal id)
    issue_key TEXT NOT NULL,                -- PROJ-123
    summary TEXT NOT NULL,
    description JSONB,                      -- ADF doc or NULL
    issue_type TEXT NOT NULL DEFAULT 'Task',
    status TEXT NOT NULL DEFAULT 'To Do',
    status_category TEXT NOT NULL DEFAULT 'new',  -- new | indeterminate | done
    priority TEXT NOT NULL DEFAULT 'Medium',
    resolution TEXT,
    resolution_date TIMESTAMPTZ,
    assignee_account_id TEXT,
    reporter_account_id TEXT,
    creator_account_id TEXT,
    labels JSONB NOT NULL DEFAULT '[]'::jsonb,
    components JSONB NOT NULL DEFAULT '[]'::jsonb,
    story_points DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,        -- JQL `updated` high-water (minute precision)
    timeline_event_id UUID REFERENCES timeline.events(id),
    UNIQUE (installation_pk, issue_key)
);
CREATE INDEX IF NOT EXISTS jira_issues_proj_updated_idx ON app_jira.issues(project_pk, updated_at);

CREATE TABLE IF NOT EXISTS app_jira.changelogs (
    id UUID PRIMARY KEY,
    issue_pk UUID NOT NULL REFERENCES app_jira.issues(id) ON DELETE CASCADE,
    history_id TEXT NOT NULL,               -- numeric string (immutable)
    author_account_id TEXT,
    items JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{field, fieldtype, fieldId, from, fromString, to, toString}]
    created_at TIMESTAMPTZ NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    UNIQUE (issue_pk, history_id)
);

CREATE TABLE IF NOT EXISTS app_jira.comments (
    id UUID PRIMARY KEY,
    issue_pk UUID NOT NULL REFERENCES app_jira.issues(id) ON DELETE CASCADE,
    comment_id TEXT NOT NULL,               -- numeric string
    author_account_id TEXT,
    body JSONB,                             -- ADF doc
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    UNIQUE (issue_pk, comment_id)
);
