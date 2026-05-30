-- =============================================================================
-- app_drive.*  — Google Drive v3 projection state
--
-- One installation per run (the Workspace customer + service account the
-- consumer impersonates via domain-wide delegation), one ``drive`` row per
-- ingest target (a person's My Drive, sentinel drive_id 'my-drive', or an org
-- Shared Drive), and one row per file. ``version`` is Drive's monotonic content
-- version (the consumer's versioned external_id key); ``change_seq`` orders the
-- synthetic changes feed that ``changes.list`` pages through. Comments and
-- revisions hang off a file. Removed/trashed files still surface (trashed=true)
-- and in the changes feed (removed=true).
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS app_drive;

CREATE TABLE IF NOT EXISTS app_drive.installations (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    customer_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    service_account_email TEXT NOT NULL,
    service_account_client_id TEXT NOT NULL,
    service_account_private_key TEXT NOT NULL,
    service_account_public_key TEXT NOT NULL,
    UNIQUE (run_id)
);

CREATE TABLE IF NOT EXISTS app_drive.drives (
    id UUID PRIMARY KEY,
    installation_pk UUID NOT NULL REFERENCES app_drive.installations(id) ON DELETE CASCADE,
    drive_id TEXT NOT NULL,                -- 'my-drive' sentinel OR a shared-drive id
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'my_drive', -- 'my_drive' | 'shared_drive'
    owner_person_id UUID REFERENCES org.people(id),  -- set for a My Drive
    owner_email TEXT,                      -- the impersonated user for this corpus
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (installation_pk, drive_id)
);

CREATE TABLE IF NOT EXISTS app_drive.files (
    id UUID PRIMARY KEY,
    installation_pk UUID NOT NULL REFERENCES app_drive.installations(id) ON DELETE CASCADE,
    drive_pk UUID NOT NULL REFERENCES app_drive.drives(id) ON DELETE CASCADE,
    file_id TEXT NOT NULL,
    name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    version BIGINT NOT NULL DEFAULT 1,     -- Drive monotonic content version
    trashed BOOLEAN NOT NULL DEFAULT FALSE,
    explicitly_trashed BOOLEAN NOT NULL DEFAULT FALSE,
    size BIGINT,                           -- NULL for Google-native docs
    web_view_link TEXT,
    owner_email TEXT,
    owner_name TEXT,
    last_modifying_email TEXT,
    last_modifying_name TEXT,
    parents JSONB NOT NULL DEFAULT '[]'::jsonb,
    shared BOOLEAN NOT NULL DEFAULT FALSE,
    starred BOOLEAN NOT NULL DEFAULT FALSE,
    extracted_text TEXT,                   -- exported/extracted body (Docs/Sheets/Slides/PDF/text)
    created_time TIMESTAMPTZ NOT NULL,
    modified_time TIMESTAMPTZ NOT NULL,    -- high-water for changes feed ordering
    change_seq BIGINT NOT NULL,            -- position in the synthetic changes log
    timeline_event_id UUID REFERENCES timeline.events(id),
    UNIQUE (installation_pk, file_id)
);
CREATE INDEX IF NOT EXISTS drive_files_inst_seq_idx ON app_drive.files(installation_pk, change_seq);
CREATE INDEX IF NOT EXISTS drive_files_drive_modified_idx ON app_drive.files(drive_pk, modified_time);

CREATE TABLE IF NOT EXISTS app_drive.comments (
    id UUID PRIMARY KEY,
    file_pk UUID NOT NULL REFERENCES app_drive.files(id) ON DELETE CASCADE,
    comment_id TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    author_name TEXT,
    author_email TEXT,
    resolved BOOLEAN NOT NULL DEFAULT FALSE,
    quoted_value TEXT,
    replies JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_time TIMESTAMPTZ NOT NULL,
    modified_time TIMESTAMPTZ NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    UNIQUE (file_pk, comment_id)
);

CREATE TABLE IF NOT EXISTS app_drive.revisions (
    id UUID PRIMARY KEY,
    file_pk UUID NOT NULL REFERENCES app_drive.files(id) ON DELETE CASCADE,
    revision_id TEXT NOT NULL,
    keep_forever BOOLEAN NOT NULL DEFAULT FALSE,
    published BOOLEAN NOT NULL DEFAULT FALSE,
    size BIGINT,
    last_modifying_email TEXT,
    last_modifying_name TEXT,
    modified_time TIMESTAMPTZ NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    UNIQUE (file_pk, revision_id)
);
