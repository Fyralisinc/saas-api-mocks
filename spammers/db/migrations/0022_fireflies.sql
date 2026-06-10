-- =============================================================================
-- app_fireflies.*  — Fireflies.ai (AI meeting-notetaker) state
--
-- One Fireflies "workspace" (the team behind one API key) per run, plus a stream
-- of meeting TRANSCRIPTS — the single Fireflies signal. Fireflies is an attesting
-- agent: it transcribes what humans said, so a transcript is a signal, not a
-- system of record.
--
-- Fireflies' REAL API (api.fireflies.ai) is **GraphQL** — a single
-- ``POST /graphql`` exposing ``transcripts``/``transcript``/``user`` queries —
-- NOTHING like the fake Brex REST surface (``GET /workspace``,
-- ``GET /transcripts``, ``GET /transcript/{id}``) the Fyralis flow doc clones (it
-- flags every read path / pagination / rate-limit signal TODO(human)/UNVERIFIED).
-- The mock serves the REAL GraphQL contract (pinned from docs.fireflies.ai); the
-- Fyralis-vs-real divergences are LOGGED in the fireflies-fidelity-audit memory,
-- not papered over.
--
-- The load-bearing wire facts this schema serves:
--   * READS are GraphQL queries over ``POST /graphql``:
--       transcripts(skip, limit≤50, fromDate, toDate, …)  → [Transcript]  (plain
--         array under data.transcripts; NO total/pageInfo — a short page = EOF)
--       transcript(id: String!)                           → Transcript    (single)
--       user(id: String)                                  → User (no id = key owner;
--                                                           the real "verify token")
--   * A Transcript's ``date`` is a **Float = epoch MILLISECONDS** (creation, UTC);
--     ``dateString`` is the separate ISO-8601 ``…Z`` string (ms precision);
--     ``duration`` is a Number in **MINUTES** (not seconds). There is NO
--     ``updatedAt``/``processedAt``/``version`` field — the dedup version is
--     DERIVED (date / meeting_info.summary_status).
--   * Fireflies has NO "workspace id" concept in the API — identity is implicit in
--     the API key (the ``user{}`` owner). We carry an ``owner_user_id`` as the
--     stable identity the ``user`` query returns + the connector's dedup namespace.
--   * Webhooks are THIN (``x-hub-signature: sha256=<hex>`` HMAC-SHA256 over the
--     body) carrying only ``meeting_id`` + an event name → fetch-on-notify
--     ``transcript(id:)``.
--
-- ``sort_key`` is a monotonic per-transcript integer giving the deterministic
-- newest-first list order; ``skip``/``limit`` page over it.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS app_fireflies;

CREATE TABLE IF NOT EXISTS app_fireflies.workspaces (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    base_url TEXT NOT NULL,                   -- https://api.fireflies.ai (or the mock)
    team_name TEXT NOT NULL,                  -- display name of the team/workspace
    api_token TEXT NOT NULL,                  -- the long-lived Bearer API key
    webhook_secret TEXT NOT NULL,             -- x-hub-signature HMAC-SHA256 key
    -- The API-key owner — what `user{}` (no id) returns; the implicit "workspace"
    -- identity (Fireflies has no first-class workspace id).
    owner_user_id TEXT NOT NULL,              -- User.user_id (stable identity)
    owner_email TEXT NOT NULL,
    owner_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (run_id)
);

-- Fireflies transcripts (one per completed meeting) — the primary (only) stream.
-- The wire ``Transcript`` is projected from these columns by dto.transcript_dto.
CREATE TABLE IF NOT EXISTS app_fireflies.transcripts (
    id UUID PRIMARY KEY,
    workspace_pk UUID NOT NULL REFERENCES app_fireflies.workspaces(id) ON DELETE CASCADE,
    transcript_id TEXT NOT NULL,              -- wire `id` — a short ~10-char alnum id
    title TEXT NOT NULL,
    -- ``meeting_date`` is the creation instant; the wire ``date`` renders it as a
    -- Float epoch-MILLISECONDS and ``dateString`` as the ISO-8601 …Z string.
    meeting_date TIMESTAMPTZ NOT NULL,
    duration_minutes DOUBLE PRECISION NOT NULL DEFAULT 0,  -- wire `duration` (MINUTES)
    organizer_email TEXT,
    host_email TEXT,
    participants JSONB NOT NULL DEFAULT '[]'::jsonb,        -- [String] (emails)
    fireflies_users JSONB NOT NULL DEFAULT '[]'::jsonb,     -- [String] (emails)
    meeting_attendees JSONB NOT NULL DEFAULT '[]'::jsonb,   -- [{displayName,email,…}]
    speakers JSONB NOT NULL DEFAULT '[]'::jsonb,            -- [{id,name}]
    summary JSONB,                                          -- Summary {overview, action_items, …}
    sentences JSONB,                                        -- [Sentence] (transcript body)
    meeting_info JSONB,                                     -- {fred_joined, silent_meeting, summary_status}
    calendar_id TEXT,
    transcript_url TEXT,
    audio_url TEXT,
    video_url TEXT,
    meeting_link TEXT,
    client_reference_id TEXT,
    -- A monotonically-bumped content version. The WIRE transcript carries NO
    -- version field (the connector derives one from date/summary_status); we keep
    -- this internal counter so a re-summarized transcript (live re-delivery) can
    -- advance summary_status without the wire gaining a field.
    version INTEGER NOT NULL DEFAULT 1,
    sort_key BIGINT NOT NULL DEFAULT 0,        -- newest-first list order (desc)
    is_historical BOOLEAN NOT NULL DEFAULT TRUE,
    timeline_event_id UUID,
    UNIQUE (workspace_pk, transcript_id)
);
CREATE INDEX IF NOT EXISTS fireflies_transcripts_ws_idx
    ON app_fireflies.transcripts(workspace_pk, sort_key DESC, transcript_id);
CREATE INDEX IF NOT EXISTS fireflies_transcripts_date_idx
    ON app_fireflies.transcripts(workspace_pk, meeting_date DESC);
