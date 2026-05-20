-- spammers/db/migrations/0001_init.sql
--
-- Mock-Postgres schema. This database is SEPARATE from Fyralis's database.
-- Default DB name: `mock_orgs`. Override via SPAMMERS_DB_URL.
--
-- Schemas:
--   org.*         — synthetic organization (people, teams, projects)
--   timeline.*    — single source of narrative truth
--   app_slack.*   — Slack-projection state
--   app_discord.* — Discord-projection state
--   app_github.*  — GitHub-projection state
--   app_gmail.*   — Gmail-projection state
--   oauth.*       — install / token / state state

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS org;
CREATE SCHEMA IF NOT EXISTS timeline;
CREATE SCHEMA IF NOT EXISTS app_slack;
CREATE SCHEMA IF NOT EXISTS app_discord;
CREATE SCHEMA IF NOT EXISTS app_github;
CREATE SCHEMA IF NOT EXISTS app_gmail;
CREATE SCHEMA IF NOT EXISTS oauth;

-- =============================================================================
-- org.*
-- =============================================================================

CREATE TABLE IF NOT EXISTS org.runs (
    id UUID PRIMARY KEY,
    size TEXT NOT NULL CHECK (size IN ('small', 'medium', 'large')),
    runtime TEXT NOT NULL CHECK (runtime IN ('few_months', 'one_year', 'few_years')),
    seed BIGINT NOT NULL,
    archetype TEXT NOT NULL DEFAULT 'early_saas',
    fyralis_tenant_id UUID NOT NULL,
    fyralis_base_url TEXT NOT NULL,
    virtual_now TIMESTAMPTZ NOT NULL,                -- updated by Director
    mode TEXT NOT NULL DEFAULT 'frozen',             -- 'frozen'|'live'|'step'
    speed_multiplier REAL NOT NULL DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finalized_at TIMESTAMPTZ                          -- set when OrgGen completes
);

CREATE TABLE IF NOT EXISTS org.teams (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    parent_id UUID REFERENCES org.teams(id),
    UNIQUE (run_id, name)
);

CREATE TABLE IF NOT EXISTS org.people (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    handle TEXT NOT NULL,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL,
    role TEXT NOT NULL,
    level TEXT NOT NULL,
    team_id UUID REFERENCES org.teams(id),
    timezone TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    voice_signature JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (run_id, handle),
    UNIQUE (run_id, email)
);
CREATE INDEX IF NOT EXISTS people_run_team_idx ON org.people(run_id, team_id);

CREATE TABLE IF NOT EXISTS org.projects (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    owner_id UUID REFERENCES org.people(id),
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    repos JSONB NOT NULL DEFAULT '[]'::jsonb,
    slack_channels JSONB NOT NULL DEFAULT '[]'::jsonb,
    discord_channels JSONB NOT NULL DEFAULT '[]'::jsonb,
    email_thread_anchors JSONB NOT NULL DEFAULT '[]'::jsonb,
    UNIQUE (run_id, slug)
);

-- =============================================================================
-- timeline.events
-- =============================================================================

CREATE TABLE IF NOT EXISTS timeline.events (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    virtual_ts TIMESTAMPTZ NOT NULL,
    type TEXT NOT NULL,
    actor_id UUID NOT NULL REFERENCES org.people(id),
    project_id UUID REFERENCES org.projects(id),
    payload JSONB NOT NULL,
    cross_refs JSONB NOT NULL DEFAULT '{}'::jsonb,
    emitted_at TIMESTAMPTZ,           -- NULL = not yet emitted (or pull-only)
    is_historical BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS events_run_ts_idx ON timeline.events(run_id, virtual_ts);
CREATE INDEX IF NOT EXISTS events_run_type_ts_idx ON timeline.events(run_id, type, virtual_ts);
CREATE INDEX IF NOT EXISTS events_pending_emission_idx
    ON timeline.events(run_id, virtual_ts)
    WHERE emitted_at IS NULL AND is_historical = FALSE;

-- =============================================================================
-- app_slack.*
-- =============================================================================

CREATE TABLE IF NOT EXISTS app_slack.workspaces (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    team_id TEXT NOT NULL,              -- 'T012345'
    team_name TEXT NOT NULL,
    team_domain TEXT NOT NULL,
    enterprise_id TEXT,
    enterprise_name TEXT,
    signing_secret TEXT NOT NULL,       -- HMAC-SHA256 secret Fyralis verifies with
    client_id TEXT NOT NULL,
    client_secret TEXT NOT NULL,
    bot_token TEXT NOT NULL,            -- 'xoxb-…'
    bot_user_id TEXT NOT NULL,
    app_id TEXT NOT NULL,
    UNIQUE (run_id, team_id)
);

CREATE TABLE IF NOT EXISTS app_slack.users (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES app_slack.workspaces(id) ON DELETE CASCADE,
    person_id UUID NOT NULL REFERENCES org.people(id),
    slack_user_id TEXT NOT NULL,        -- 'U012345'
    is_bot BOOLEAN NOT NULL DEFAULT FALSE,
    deleted BOOLEAN NOT NULL DEFAULT FALSE,
    profile JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (workspace_id, slack_user_id),
    UNIQUE (workspace_id, person_id)
);

CREATE TABLE IF NOT EXISTS app_slack.channels (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES app_slack.workspaces(id) ON DELETE CASCADE,
    channel_id TEXT NOT NULL,           -- 'C012345'
    name TEXT NOT NULL,
    is_private BOOLEAN NOT NULL DEFAULT FALSE,
    is_archived BOOLEAN NOT NULL DEFAULT FALSE,
    is_im BOOLEAN NOT NULL DEFAULT FALSE,
    is_mpim BOOLEAN NOT NULL DEFAULT FALSE,
    is_general BOOLEAN NOT NULL DEFAULT FALSE,
    topic TEXT NOT NULL DEFAULT '',
    purpose TEXT NOT NULL DEFAULT '',
    creator_user_id TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (workspace_id, channel_id),
    UNIQUE (workspace_id, name)
);

CREATE TABLE IF NOT EXISTS app_slack.channel_membership (
    channel_pk UUID NOT NULL REFERENCES app_slack.channels(id) ON DELETE CASCADE,
    user_pk UUID NOT NULL REFERENCES app_slack.users(id) ON DELETE CASCADE,
    joined_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (channel_pk, user_pk)
);

CREATE TABLE IF NOT EXISTS app_slack.messages (
    id UUID PRIMARY KEY,
    channel_pk UUID NOT NULL REFERENCES app_slack.channels(id) ON DELETE CASCADE,
    user_pk UUID REFERENCES app_slack.users(id),
    ts TEXT NOT NULL,                   -- '1626825612.000200'
    thread_ts TEXT,                     -- present on replies
    subtype TEXT,                       -- NULL for plain messages
    text TEXT NOT NULL,
    blocks JSONB,
    attachments JSONB,
    reply_count INTEGER NOT NULL DEFAULT 0,
    reactions JSONB NOT NULL DEFAULT '[]'::jsonb,
    edited JSONB,
    is_hidden BOOLEAN NOT NULL DEFAULT FALSE,
    timeline_event_id UUID REFERENCES timeline.events(id),
    UNIQUE (channel_pk, ts)
);
CREATE INDEX IF NOT EXISTS slack_msgs_channel_ts_idx
    ON app_slack.messages(channel_pk, ts DESC);
CREATE INDEX IF NOT EXISTS slack_msgs_thread_idx
    ON app_slack.messages(channel_pk, thread_ts);

-- =============================================================================
-- app_discord.*
-- =============================================================================

CREATE TABLE IF NOT EXISTS app_discord.applications (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    application_id TEXT NOT NULL,       -- numeric snowflake string
    client_id TEXT NOT NULL,
    client_secret TEXT NOT NULL,
    bot_token TEXT NOT NULL,
    public_key TEXT NOT NULL,           -- Ed25519 hex
    private_key TEXT NOT NULL,          -- Ed25519 hex (mock-side only)
    UNIQUE (run_id, application_id)
);

CREATE TABLE IF NOT EXISTS app_discord.guilds (
    id UUID PRIMARY KEY,
    application_pk UUID NOT NULL REFERENCES app_discord.applications(id) ON DELETE CASCADE,
    guild_id TEXT NOT NULL,
    name TEXT NOT NULL,
    icon_hash TEXT,
    owner_user_id TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (application_pk, guild_id)
);

CREATE TABLE IF NOT EXISTS app_discord.users (
    id UUID PRIMARY KEY,
    application_pk UUID NOT NULL REFERENCES app_discord.applications(id) ON DELETE CASCADE,
    person_id UUID NOT NULL REFERENCES org.people(id),
    discord_user_id TEXT NOT NULL,
    username TEXT NOT NULL,
    discriminator TEXT NOT NULL DEFAULT '0',
    avatar_hash TEXT,
    is_bot BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (application_pk, discord_user_id),
    UNIQUE (application_pk, person_id)
);

CREATE TABLE IF NOT EXISTS app_discord.channels (
    id UUID PRIMARY KEY,
    guild_pk UUID NOT NULL REFERENCES app_discord.guilds(id) ON DELETE CASCADE,
    channel_id TEXT NOT NULL,
    name TEXT NOT NULL,
    type INTEGER NOT NULL DEFAULT 0,    -- 0=GUILD_TEXT, 2=VOICE, 4=CATEGORY, 11=PUBLIC_THREAD, ...
    parent_id TEXT,
    topic TEXT,
    nsfw BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (guild_pk, channel_id),
    UNIQUE (guild_pk, name)
);

CREATE TABLE IF NOT EXISTS app_discord.messages (
    id UUID PRIMARY KEY,
    channel_pk UUID NOT NULL REFERENCES app_discord.channels(id) ON DELETE CASCADE,
    message_id TEXT NOT NULL,           -- snowflake string
    author_user_pk UUID REFERENCES app_discord.users(id),
    content TEXT NOT NULL,
    type INTEGER NOT NULL DEFAULT 0,
    pinned BOOLEAN NOT NULL DEFAULT FALSE,
    mentions JSONB NOT NULL DEFAULT '[]'::jsonb,
    attachments JSONB NOT NULL DEFAULT '[]'::jsonb,
    embeds JSONB NOT NULL DEFAULT '[]'::jsonb,
    reactions JSONB NOT NULL DEFAULT '[]'::jsonb,
    referenced_message_id TEXT,
    thread_id TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    edited_at TIMESTAMPTZ,
    timeline_event_id UUID REFERENCES timeline.events(id),
    UNIQUE (channel_pk, message_id)
);
CREATE INDEX IF NOT EXISTS discord_msgs_channel_created_idx
    ON app_discord.messages(channel_pk, created_at DESC);

-- discord interaction registry (slash commands)
CREATE TABLE IF NOT EXISTS app_discord.commands (
    id UUID PRIMARY KEY,
    application_pk UUID NOT NULL REFERENCES app_discord.applications(id) ON DELETE CASCADE,
    command_id TEXT NOT NULL,           -- snowflake
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    type INTEGER NOT NULL DEFAULT 1,
    options JSONB NOT NULL DEFAULT '[]'::jsonb,
    UNIQUE (application_pk, name)
);

-- =============================================================================
-- app_github.*
-- =============================================================================

CREATE TABLE IF NOT EXISTS app_github.apps (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    app_id BIGINT NOT NULL,                 -- numeric id used in JWT iss
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    client_id TEXT NOT NULL,
    client_secret TEXT NOT NULL,
    webhook_secret TEXT NOT NULL,
    private_key TEXT NOT NULL,              -- RSA PEM (for Fyralis to sign JWTs)
    public_key TEXT NOT NULL,               -- RSA PEM (mock uses to verify)
    permissions JSONB NOT NULL DEFAULT '{}'::jsonb,
    events JSONB NOT NULL DEFAULT '[]'::jsonb,
    UNIQUE (run_id, app_id),
    UNIQUE (run_id, slug)
);

CREATE TABLE IF NOT EXISTS app_github.installations (
    id UUID PRIMARY KEY,
    app_pk UUID NOT NULL REFERENCES app_github.apps(id) ON DELETE CASCADE,
    installation_id BIGINT NOT NULL,
    account_login TEXT NOT NULL,
    account_type TEXT NOT NULL DEFAULT 'Organization',
    account_id BIGINT NOT NULL,
    repository_selection TEXT NOT NULL DEFAULT 'all',
    suspended_at TIMESTAMPTZ,
    suspended_by TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (app_pk, installation_id)
);

CREATE TABLE IF NOT EXISTS app_github.repositories (
    id UUID PRIMARY KEY,
    installation_pk UUID NOT NULL REFERENCES app_github.installations(id) ON DELETE CASCADE,
    repo_id BIGINT NOT NULL,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    full_name TEXT GENERATED ALWAYS AS (owner || '/' || name) STORED,
    private BOOLEAN NOT NULL DEFAULT FALSE,
    default_branch TEXT NOT NULL DEFAULT 'main',
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (installation_pk, repo_id),
    UNIQUE (installation_pk, owner, name)
);

CREATE TABLE IF NOT EXISTS app_github.pull_requests (
    id UUID PRIMARY KEY,
    repo_pk UUID NOT NULL REFERENCES app_github.repositories(id) ON DELETE CASCADE,
    number INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'open',         -- 'open'|'closed'
    merged BOOLEAN NOT NULL DEFAULT FALSE,
    user_login TEXT NOT NULL,
    head_ref TEXT NOT NULL,
    head_sha TEXT NOT NULL,
    base_ref TEXT NOT NULL DEFAULT 'main',
    base_sha TEXT NOT NULL,
    additions INTEGER NOT NULL DEFAULT 0,
    deletions INTEGER NOT NULL DEFAULT 0,
    changed_files INTEGER NOT NULL DEFAULT 0,
    labels JSONB NOT NULL DEFAULT '[]'::jsonb,
    requested_reviewers JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    merged_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    timeline_event_id UUID REFERENCES timeline.events(id),
    UNIQUE (repo_pk, number)
);

CREATE TABLE IF NOT EXISTS app_github.issues (
    id UUID PRIMARY KEY,
    repo_pk UUID NOT NULL REFERENCES app_github.repositories(id) ON DELETE CASCADE,
    number INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'open',
    user_login TEXT NOT NULL,
    assignees JSONB NOT NULL DEFAULT '[]'::jsonb,
    labels JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    closed_at TIMESTAMPTZ,
    timeline_event_id UUID REFERENCES timeline.events(id),
    UNIQUE (repo_pk, number)
);

CREATE TABLE IF NOT EXISTS app_github.commits (
    id UUID PRIMARY KEY,
    repo_pk UUID NOT NULL REFERENCES app_github.repositories(id) ON DELETE CASCADE,
    sha TEXT NOT NULL,
    message TEXT NOT NULL,
    author_login TEXT NOT NULL,
    author_email TEXT NOT NULL,
    committed_at TIMESTAMPTZ NOT NULL,
    parents JSONB NOT NULL DEFAULT '[]'::jsonb,
    additions INTEGER NOT NULL DEFAULT 0,
    deletions INTEGER NOT NULL DEFAULT 0,
    UNIQUE (repo_pk, sha)
);

CREATE TABLE IF NOT EXISTS app_github.reviews (
    id UUID PRIMARY KEY,
    pr_pk UUID NOT NULL REFERENCES app_github.pull_requests(id) ON DELETE CASCADE,
    user_login TEXT NOT NULL,
    state TEXT NOT NULL,    -- 'approved'|'commented'|'changes_requested'|'dismissed'
    body TEXT NOT NULL DEFAULT '',
    submitted_at TIMESTAMPTZ NOT NULL,
    timeline_event_id UUID REFERENCES timeline.events(id)
);

CREATE TABLE IF NOT EXISTS app_github.issue_comments (
    id UUID PRIMARY KEY,
    repo_pk UUID NOT NULL REFERENCES app_github.repositories(id) ON DELETE CASCADE,
    issue_number INTEGER NOT NULL,
    user_login TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    timeline_event_id UUID REFERENCES timeline.events(id)
);

CREATE TABLE IF NOT EXISTS app_github.check_runs (
    id UUID PRIMARY KEY,
    repo_pk UUID NOT NULL REFERENCES app_github.repositories(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    head_sha TEXT NOT NULL,
    status TEXT NOT NULL,                       -- 'queued'|'in_progress'|'completed'
    conclusion TEXT,                            -- 'success'|'failure'|'neutral'|'cancelled'|'skipped'|'timed_out'|'action_required'
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    timeline_event_id UUID REFERENCES timeline.events(id)
);

-- =============================================================================
-- app_gmail.*
-- =============================================================================

CREATE TABLE IF NOT EXISTS app_gmail.customers (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    customer_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    organization_name TEXT NOT NULL,
    service_account_email TEXT NOT NULL,
    service_account_public_key TEXT NOT NULL,
    pubsub_oidc_public_key TEXT NOT NULL,
    pubsub_oidc_private_key TEXT NOT NULL,
    pubsub_audience TEXT NOT NULL,
    UNIQUE (run_id, customer_id),
    UNIQUE (run_id, domain)
);

CREATE TABLE IF NOT EXISTS app_gmail.mailboxes (
    id UUID PRIMARY KEY,
    customer_pk UUID NOT NULL REFERENCES app_gmail.customers(id) ON DELETE CASCADE,
    person_id UUID NOT NULL REFERENCES org.people(id),
    email TEXT NOT NULL,
    history_id BIGINT NOT NULL DEFAULT 1,
    profile JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (customer_pk, email)
);

CREATE TABLE IF NOT EXISTS app_gmail.threads (
    id UUID PRIMARY KEY,
    mailbox_pk UUID NOT NULL REFERENCES app_gmail.mailboxes(id) ON DELETE CASCADE,
    thread_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    snippet TEXT NOT NULL DEFAULT '',
    UNIQUE (mailbox_pk, thread_id)
);

CREATE TABLE IF NOT EXISTS app_gmail.messages (
    id UUID PRIMARY KEY,
    thread_pk UUID NOT NULL REFERENCES app_gmail.threads(id) ON DELETE CASCADE,
    message_id TEXT NOT NULL,
    history_id BIGINT NOT NULL,
    rfc822_msg_id TEXT NOT NULL,
    label_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    headers JSONB NOT NULL DEFAULT '[]'::jsonb,
    snippet TEXT NOT NULL DEFAULT '',
    body_plain TEXT NOT NULL DEFAULT '',
    body_html TEXT NOT NULL DEFAULT '',
    internal_date TIMESTAMPTZ NOT NULL,
    size_estimate INTEGER NOT NULL DEFAULT 0,
    timeline_event_id UUID REFERENCES timeline.events(id),
    UNIQUE (thread_pk, message_id)
);
CREATE INDEX IF NOT EXISTS gmail_msgs_mailbox_internal_idx
    ON app_gmail.messages(thread_pk, internal_date DESC);

CREATE TABLE IF NOT EXISTS app_gmail.history (
    id BIGSERIAL PRIMARY KEY,
    mailbox_pk UUID NOT NULL REFERENCES app_gmail.mailboxes(id) ON DELETE CASCADE,
    history_id BIGINT NOT NULL,
    history_type TEXT NOT NULL,         -- 'messageAdded'|'labelAdded'|'labelRemoved'|'messageDeleted'
    message_id TEXT,
    thread_id TEXT,
    label_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS gmail_history_mailbox_hid_idx
    ON app_gmail.history(mailbox_pk, history_id);

CREATE TABLE IF NOT EXISTS app_gmail.watches (
    id UUID PRIMARY KEY,
    mailbox_pk UUID NOT NULL REFERENCES app_gmail.mailboxes(id) ON DELETE CASCADE,
    topic_name TEXT NOT NULL,
    label_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    label_filter_action TEXT NOT NULL DEFAULT 'include',
    expiration TIMESTAMPTZ NOT NULL,
    started_history_id BIGINT NOT NULL,
    UNIQUE (mailbox_pk, topic_name)
);

-- =============================================================================
-- oauth.*
-- =============================================================================

CREATE TABLE IF NOT EXISTS oauth.installs (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,                     -- 'slack'|'discord'|'github'|'gmail'
    fyralis_tenant_id UUID NOT NULL,
    provider_account_id TEXT NOT NULL,          -- team_id / guild_id / installation_id / customer_id
    access_token TEXT NOT NULL,
    refresh_token TEXT,
    bot_token TEXT,                             -- for slack 'xoxb-…'
    expires_at TIMESTAMPTZ,
    scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
    extra JSONB NOT NULL DEFAULT '{}'::jsonb,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, provider, provider_account_id)
);

CREATE TABLE IF NOT EXISTS oauth.codes (
    id UUID PRIMARY KEY,
    install_pk UUID REFERENCES oauth.installs(id) ON DELETE CASCADE,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    code TEXT NOT NULL UNIQUE,
    redirect_uri TEXT NOT NULL,
    state TEXT NOT NULL,
    issued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    consumed_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '10 minutes')
);

CREATE TABLE IF NOT EXISTS oauth.states (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    state TEXT NOT NULL UNIQUE,
    redirect_uri TEXT NOT NULL,
    scope TEXT,
    issued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    consumed_at TIMESTAMPTZ
);
