-- =============================================================================
-- app_notion.*  — Notion projection state (API version 2022-06-28)
--
-- One integration (bot) per run. Content is a small workspace: a few databases,
-- pages (database rows + loose pages), the blocks under each page, and comments.
-- The consumer backfills by tree-walking search → database query → block
-- children → comments, and hydrates webhook events via GET /v1/pages/{id}.
-- ``verification_token`` doubles as the HMAC secret the webhook is signed with.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS app_notion;

CREATE TABLE IF NOT EXISTS app_notion.integrations (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    bot_token TEXT NOT NULL,                 -- 'secret_…' / 'ntn_…' bearer
    workspace_id TEXT NOT NULL,
    workspace_name TEXT NOT NULL,
    bot_user_id TEXT NOT NULL,
    bot_name TEXT NOT NULL DEFAULT 'Ingest Bot',
    client_id TEXT NOT NULL,
    client_secret TEXT NOT NULL,
    verification_token TEXT NOT NULL,        -- webhook handshake + HMAC secret
    UNIQUE (run_id)
);

CREATE TABLE IF NOT EXISTS app_notion.databases (
    id UUID PRIMARY KEY,
    integration_pk UUID NOT NULL REFERENCES app_notion.integrations(id) ON DELETE CASCADE,
    database_id TEXT NOT NULL,               -- dashed UUID, Notion-style
    title TEXT NOT NULL,
    parent_type TEXT NOT NULL DEFAULT 'workspace',
    parent_id TEXT,
    icon TEXT,
    properties_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
    url TEXT NOT NULL,
    created_time TIMESTAMPTZ NOT NULL,
    last_edited_time TIMESTAMPTZ NOT NULL,
    UNIQUE (integration_pk, database_id)
);

CREATE TABLE IF NOT EXISTS app_notion.pages (
    id UUID PRIMARY KEY,
    integration_pk UUID NOT NULL REFERENCES app_notion.integrations(id) ON DELETE CASCADE,
    page_id TEXT NOT NULL,
    parent_type TEXT NOT NULL,               -- 'database_id' | 'page_id' | 'workspace'
    parent_id TEXT,                          -- database_id / page_id (null for workspace)
    database_pk UUID REFERENCES app_notion.databases(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    properties JSONB NOT NULL DEFAULT '{}'::jsonb,
    icon TEXT,
    archived BOOLEAN NOT NULL DEFAULT FALSE,
    url TEXT NOT NULL,
    created_by TEXT,
    created_time TIMESTAMPTZ NOT NULL,
    last_edited_time TIMESTAMPTZ NOT NULL,
    timeline_event_id UUID REFERENCES timeline.events(id),
    UNIQUE (integration_pk, page_id)
);
CREATE INDEX IF NOT EXISTS notion_pages_db_idx ON app_notion.pages(database_pk);
CREATE INDEX IF NOT EXISTS notion_pages_edited_idx
    ON app_notion.pages(integration_pk, last_edited_time DESC);

CREATE TABLE IF NOT EXISTS app_notion.blocks (
    id UUID PRIMARY KEY,
    page_pk UUID NOT NULL REFERENCES app_notion.pages(id) ON DELETE CASCADE,
    block_id TEXT NOT NULL,
    parent_block_id TEXT,                    -- null = direct child of the page
    type TEXT NOT NULL,                      -- 'paragraph' | 'heading_2' | …
    content JSONB NOT NULL DEFAULT '{}'::jsonb,   -- the type-specific object
    has_children BOOLEAN NOT NULL DEFAULT FALSE,
    position INTEGER NOT NULL DEFAULT 0,
    created_by TEXT,
    created_time TIMESTAMPTZ NOT NULL,
    last_edited_time TIMESTAMPTZ NOT NULL,
    timeline_event_id UUID REFERENCES timeline.events(id),
    UNIQUE (page_pk, block_id)
);
CREATE INDEX IF NOT EXISTS notion_blocks_page_pos_idx ON app_notion.blocks(page_pk, position);

CREATE TABLE IF NOT EXISTS app_notion.comments (
    id UUID PRIMARY KEY,
    page_pk UUID NOT NULL REFERENCES app_notion.pages(id) ON DELETE CASCADE,
    comment_id TEXT NOT NULL,
    discussion_id TEXT NOT NULL,
    parent_page_id TEXT NOT NULL,            -- the page id comments are queried by
    rich_text JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_by TEXT,
    created_time TIMESTAMPTZ NOT NULL,
    last_edited_time TIMESTAMPTZ NOT NULL,
    UNIQUE (page_pk, comment_id)
);
CREATE INDEX IF NOT EXISTS notion_comments_parent_idx ON app_notion.comments(parent_page_id);
