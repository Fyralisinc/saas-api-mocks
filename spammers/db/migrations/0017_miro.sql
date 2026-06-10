-- =============================================================================
-- app_miro.*  — Miro collaborative-whiteboard projection state
--
-- One Miro ORG (the tenant) per run, authenticated by a single long-lived
-- org-level app Bearer token (scope ``boards:read``). Under the org hang USERS
-- (board owners / item authors), BOARDS, and per-board ITEMS (sticky notes,
-- shapes, text, cards, frames). ``org_id`` namespaces every ingestion
-- ``external_id`` (miro:{org_id}:item:{item_id}:{version}).
--
-- The REAL Miro REST API (api.miro.com, ``/v2/``) is NOT exactly the Brex-Bearer
-- archetype clone the Fyralis flow doc carries (which it flags TODO(human)/
-- UNVERIFIED — "everything is the same paginator"). The mock honours the REAL
-- wire contract (pinned from Miro's published OpenAPI spec, the source Miro
-- generates all its SDK clients from); the Fyralis-vs-real divergences are
-- LOGGED in the miro-fidelity-audit memory, not papered over. The big ones:
--   * ``GET /v2/boards`` is OFFSET-paginated (``limit``/``offset``,
--     ``{data,total,size,offset,limit,links,type}``) while
--     ``GET /v2/boards/{id}/items`` is CURSOR-paginated (opaque ``cursor``,
--     ``limit`` 10-50, ``{data,total,size,cursor,limit,links}`` — NO top-level
--     ``type``; the ``cursor`` field is ABSENT on the last page) — TWO DIFFERENT
--     paginators, not one;
--   * Miro items have NO version field — only ``createdAt``/``modifiedAt`` (ms-Z
--     ISO-8601); a poller diffs on ``modifiedAt`` (the dedup ``{version}`` segment
--     falls back to it);
--   * item ``createdBy``/``modifiedBy`` are ``{id, type:"user"}`` with NO ``name``
--     (board ``owner``/``createdBy``/``modifiedBy`` DO carry ``name``);
--   * rate-limit is CREDIT-based: 429 + ``X-RateLimit-Limit/Remaining/Reset``
--     headers and NO ``Retry-After`` (a real divergence from the Brex archetype);
--   * **Miro discontinued its experimental webhooks on 2025-12-05 → POLL-ONLY**:
--     there is NO production push, no signature scheme; the live story is the
--     reconciler re-walking ``/items`` and dedup'ing via the versioned external_id.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS app_miro;

-- One Miro org per run — the tenant + its credentials. ``org_id`` namespaces every
-- ingestion ``external_id``; ``access_token`` is the org-app Bearer the reads
-- present (the mock accepts any non-empty Bearer). ``team_id``/``team_name`` are the
-- Miro team emitted on every board's ``team`` object.
CREATE TABLE IF NOT EXISTS app_miro.orgs (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    base_url TEXT NOT NULL,                  -- https://api.miro.com/v2 (or the mock)
    org_id TEXT NOT NULL,                    -- the Miro org id (external_id namespace)
    org_name TEXT NOT NULL,
    team_id TEXT NOT NULL,                   -- the Miro team id (board.team.id)
    team_name TEXT NOT NULL,
    access_token TEXT NOT NULL,              -- org-app Bearer (mock accepts any non-empty)
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (run_id)
);

-- Miro user identities (board owners / item authors). On BOARD objects the user
-- shape is ``{id, name, type:"user"}`` (carries ``name``); on ITEM
-- ``createdBy``/``modifiedBy`` it is ``{id, type:"user"}`` (NO name). ``is_me`` marks
-- the ingesting service account that appears as a board's ``currentUserMembership``.
CREATE TABLE IF NOT EXISTS app_miro.users (
    id UUID PRIMARY KEY,
    org_pk UUID NOT NULL REFERENCES app_miro.orgs(id) ON DELETE CASCADE,
    person_id UUID,                          -- optional link back to org.people
    miro_user_id TEXT NOT NULL,              -- wire `id` (numeric string)
    name TEXT NOT NULL,                      -- wire `name` (board user objects only)
    role TEXT NOT NULL DEFAULT 'editor',     -- currentUserMembership role (viewer..owner)
    is_me BOOLEAN NOT NULL DEFAULT FALSE,    -- the ingesting service account (board member)
    UNIQUE (org_pk, miro_user_id)
);
CREATE INDEX IF NOT EXISTS miro_users_org_idx ON app_miro.users(org_pk);

-- Boards under the org (GET /v2/boards — offset-paged; GET /v2/boards/{id} — + links).
-- ``sort_key`` is the stable offset ordering. ``view_link`` = https://miro.com/app/board/{id}.
CREATE TABLE IF NOT EXISTS app_miro.boards (
    id UUID PRIMARY KEY,
    org_pk UUID NOT NULL REFERENCES app_miro.orgs(id) ON DELETE CASCADE,
    board_id TEXT NOT NULL,                  -- wire `id` (e.g. "uXjVOD6LSME=")
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    view_link TEXT NOT NULL,
    owner_user_pk UUID REFERENCES app_miro.users(id),
    created_by_user_pk UUID REFERENCES app_miro.users(id),
    modified_by_user_pk UUID REFERENCES app_miro.users(id),
    created_at TIMESTAMPTZ NOT NULL,         -- wire `createdAt` (ms-Z)
    modified_at TIMESTAMPTZ NOT NULL,        -- wire `modifiedAt` (ms-Z)
    last_opened_at TIMESTAMPTZ,              -- wire `lastOpenedAt` (ms-Z)
    sort_key INTEGER NOT NULL DEFAULT 0,
    UNIQUE (org_pk, board_id)
);
CREATE INDEX IF NOT EXISTS miro_boards_org_idx ON app_miro.boards(org_pk, sort_key);

-- One board item per row (GET /v2/boards/{id}/items — CURSOR-paged). ``item_seq``
-- is a monotonic integer the opaque cursor encodes (the consumer round-trips the
-- cursor verbatim). ``item_type`` is the wire ``type`` (sticky_note/shape/text/card/
-- frame/…); ``data``/``geometry``/``position`` are the type-specific wire sub-objects
-- (jsonb, emitted verbatim). ``parent_id`` = the parent frame's item id (or NULL).
-- Items have NO version field — ``modifiedAt`` is the only mutation anchor.
CREATE TABLE IF NOT EXISTS app_miro.items (
    id UUID PRIMARY KEY,
    board_pk UUID NOT NULL REFERENCES app_miro.boards(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL,                   -- wire `id` (numeric string)
    item_type TEXT NOT NULL,                 -- wire `type`
    data JSONB NOT NULL DEFAULT '{}'::jsonb, -- wire `data` (WidgetDataOutput, type-specific)
    geometry JSONB,                          -- wire `geometry` {width,height,rotation}
    position JSONB,                          -- wire `position` {x,y,origin,relativeTo}
    parent_id TEXT,                          -- wire `parent.id` (parent frame, or NULL)
    created_by_user_pk UUID REFERENCES app_miro.users(id),
    modified_by_user_pk UUID REFERENCES app_miro.users(id),
    created_at TIMESTAMPTZ NOT NULL,         -- wire `createdAt` (ms-Z)
    modified_at TIMESTAMPTZ NOT NULL,        -- wire `modifiedAt` (ms-Z)
    item_seq BIGINT NOT NULL,                -- monotonic cursor key
    is_historical BOOLEAN NOT NULL DEFAULT TRUE,
    timeline_event_id UUID,
    UNIQUE (board_pk, item_id)
);
CREATE INDEX IF NOT EXISTS miro_items_board_seq_idx
    ON app_miro.items(board_pk, item_seq ASC);
