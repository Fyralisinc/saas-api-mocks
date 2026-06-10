-- =============================================================================
-- app_figma.*  — Figma design-tool projection state
--
-- One Figma TEAM (the tenant) per run, identified by its access token (an
-- ``X-Figma-Token`` personal/plan token, or an OAuth Bearer — both accepted) plus
-- a webhook PASSCODE (the Webhooks-v2 body-passcode that authenticates a delivery
-- — Figma has NO HMAC signature). Under the team hang USERS (figma identities),
-- PROJECTS, FILES, and per-file VERSIONS + COMMENTS.
--
-- Figma's REAL API (api.figma.com, ``/v1/``) is NOT the Brex-Bearer-archetype
-- clone the Fyralis flow doc carries (which it flags TODO(human)/UNVERIFIED — it
-- hits a single ``GET /v1/files/{key}/events`` that DOES NOT EXIST). The mock
-- honours the REAL wire contract (pinned from developers.figma.com + the official
-- OpenAPI spec figma/rest-api-spec); the Fyralis-vs-real divergences are LOGGED in
-- the figma-fidelity-audit memory, not papered over. The big ones:
--   * there is NO ``/v1/files`` list and NO ``/events`` stream — a real backfill
--     ENUMERATES files (``GET /v1/teams/{id}/projects`` → ``/v1/projects/{id}/files``)
--     then MERGES ``GET /v1/files/{key}/versions`` + ``/v1/files/{key}/comments``
--     into one event stream;
--   * ``/versions`` paginates CURSOR-style (``page_size`` default 30/max 50 + numeric
--     ``before``/``after``; ``pagination.{prev_page,next_page}`` are FULL URLs) —
--     NOT offset/limit; ``/comments`` has NO pagination (one ``{comments:[…]}`` array);
--   * the webhook is a body-PASSCODE plaintext field (constant-time compare), NOT an
--     HMAC header — that part the Fyralis flow doc finally got right;
--   * the User object is ``{id, handle, img_url}`` with NO email (email is /v1/me only);
--   * timestamps are UTC ISO-8601 with ``Z``; auth failure on file reads is 403 (not
--     401) with the ``{status, err}`` err-message envelope.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS app_figma;

-- One Figma team per run — the tenant + its credentials. ``team_id`` namespaces
-- every ingestion ``external_id`` (figma:{team_id}:event:{id}:{version}) and is the
-- webhook tenant key. ``access_token`` is the X-Figma-Token / Bearer the reads
-- present; ``webhook_passcode`` is the Webhooks-v2 plaintext body passcode.
CREATE TABLE IF NOT EXISTS app_figma.teams (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    base_url TEXT NOT NULL,                  -- https://api.figma.com (or the mock)
    team_id TEXT NOT NULL,                   -- the Figma team id (external_id namespace)
    team_name TEXT NOT NULL,
    access_token TEXT NOT NULL,              -- X-Figma-Token / OAuth Bearer (mock accepts any non-empty)
    webhook_passcode TEXT NOT NULL,          -- Webhooks-v2 body passcode (plaintext compare)
    webhook_id TEXT NOT NULL,                -- the webhook's id (echoed in every delivery)
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (run_id)
);

-- Figma user identities (version authors / commenters / triggered_by). The wire
-- object is ``{id, handle, img_url}`` — NO email (email is only on /v1/me, stored
-- here so /v1/me can serve it but never emitted on a version/comment/webhook user).
CREATE TABLE IF NOT EXISTS app_figma.users (
    id UUID PRIMARY KEY,
    team_pk UUID NOT NULL REFERENCES app_figma.teams(id) ON DELETE CASCADE,
    person_id UUID,                          -- optional link back to org.people
    figma_user_id TEXT NOT NULL,             -- wire `id` (numeric string)
    handle TEXT NOT NULL,                    -- wire `handle`
    img_url TEXT NOT NULL,                   -- wire `img_url`
    email TEXT,                              -- /v1/me only; NEVER emitted on version/comment users
    is_me BOOLEAN NOT NULL DEFAULT FALSE,    -- the ingesting service account (GET /v1/me)
    UNIQUE (team_pk, figma_user_id)
);
CREATE INDEX IF NOT EXISTS figma_users_team_idx ON app_figma.users(team_pk);

-- Projects under the team (GET /v1/teams/{id}/projects).
CREATE TABLE IF NOT EXISTS app_figma.projects (
    id UUID PRIMARY KEY,
    team_pk UUID NOT NULL REFERENCES app_figma.teams(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL,                -- wire `id` (string)
    name TEXT NOT NULL,
    sort_key INTEGER NOT NULL DEFAULT 0,
    UNIQUE (team_pk, project_id)
);
CREATE INDEX IF NOT EXISTS figma_projects_team_idx ON app_figma.projects(team_pk, sort_key);

-- Design files (GET /v1/projects/{id}/files + GET /v1/files/{key}/meta). ``file_key``
-- is the wire key; ``current_version_id`` mirrors the newest version's id (the /meta
-- ``version`` field). ``folder_name`` = the containing project name (the /meta folder).
CREATE TABLE IF NOT EXISTS app_figma.files (
    id UUID PRIMARY KEY,
    team_pk UUID NOT NULL REFERENCES app_figma.teams(id) ON DELETE CASCADE,
    project_pk UUID NOT NULL REFERENCES app_figma.projects(id) ON DELETE CASCADE,
    file_key TEXT NOT NULL,                  -- wire `key`
    name TEXT NOT NULL,
    thumbnail_url TEXT NOT NULL,
    editor_type TEXT NOT NULL DEFAULT 'figma',  -- figma|figjam|slides|…
    folder_name TEXT NOT NULL,               -- /meta folder_name (the project name)
    creator_pk UUID REFERENCES app_figma.users(id),
    current_version_id TEXT,                  -- /meta `version` (newest version id)
    last_modified TIMESTAMPTZ NOT NULL,       -- /projects/{id}/files last_modified + /meta last_touched_at
    created_at TIMESTAMPTZ NOT NULL,
    sort_key INTEGER NOT NULL DEFAULT 0,
    UNIQUE (team_pk, file_key)
);
CREATE INDEX IF NOT EXISTS figma_files_project_idx ON app_figma.files(project_pk, sort_key);

-- One named/auto version per row — half of the file's event stream
-- (GET /v1/files/{key}/versions). ``version_seq`` is a monotonic integer
-- (= numeric ``version_id``) so the CURSOR ``before``/``after`` walk orders by it.
-- ``label``/``description`` are NULL for an auto-save (a real Figma wrinkle).
CREATE TABLE IF NOT EXISTS app_figma.versions (
    id UUID PRIMARY KEY,
    file_pk UUID NOT NULL REFERENCES app_figma.files(id) ON DELETE CASCADE,
    version_id TEXT NOT NULL,                 -- wire `id` (numeric string)
    version_seq BIGINT NOT NULL,              -- numeric value of version_id (cursor key)
    label TEXT,                               -- wire `label` (NULL for autosaves)
    description TEXT,                         -- wire `description` (NULL for autosaves)
    user_pk UUID NOT NULL REFERENCES app_figma.users(id),
    created_at TIMESTAMPTZ NOT NULL,          -- wire `created_at` (ISO-8601 Z)
    is_historical BOOLEAN NOT NULL DEFAULT TRUE,
    timeline_event_id UUID,
    UNIQUE (file_pk, version_id)
);
CREATE INDEX IF NOT EXISTS figma_versions_file_seq_idx
    ON app_figma.versions(file_pk, version_seq DESC);

-- One comment per row — the other half of the event stream
-- (GET /v1/files/{key}/comments, a single un-paginated array). ``parent_id`` links
-- a reply; ``resolved_at`` marks a resolved thread; ``client_meta`` is the pin
-- anchor (Vector {x,y} or FrameOffset); ``order_id`` is a string|null ordering key.
CREATE TABLE IF NOT EXISTS app_figma.comments (
    id UUID PRIMARY KEY,
    file_pk UUID NOT NULL REFERENCES app_figma.files(id) ON DELETE CASCADE,
    comment_id TEXT NOT NULL,                 -- wire `id` (numeric string)
    parent_id TEXT,                           -- wire `parent_id` (reply target, or NULL)
    user_pk UUID NOT NULL REFERENCES app_figma.users(id),
    message TEXT NOT NULL,                    -- wire `message`
    order_id TEXT,                            -- wire `order_id` (string|null per spec, not Number)
    client_meta JSONB NOT NULL DEFAULT '{}'::jsonb,  -- wire `client_meta` (Vector|FrameOffset)
    reactions JSONB NOT NULL DEFAULT '[]'::jsonb,    -- wire `reactions`
    created_at TIMESTAMPTZ NOT NULL,          -- wire `created_at` (ISO-8601 Z)
    resolved_at TIMESTAMPTZ,                  -- wire `resolved_at` (ISO-8601 Z, or null)
    sort_key BIGINT NOT NULL DEFAULT 0,
    is_historical BOOLEAN NOT NULL DEFAULT TRUE,
    timeline_event_id UUID,
    UNIQUE (file_pk, comment_id)
);
CREATE INDEX IF NOT EXISTS figma_comments_file_idx
    ON app_figma.comments(file_pk, sort_key ASC);
