-- =============================================================================
-- Corpus replay support — phase-5 scaffold.
--
-- The new code path ingests a frozen ``events.jsonl`` (one row per timed event)
-- generated in the sibling ``alpen-corpus`` repo. Replay is incremental: every
-- run carries a ``replay_cursor`` and the replayer applies events whose
-- timestamp ``<= cursor``. The corpus uses stable string identifiers
-- (``person:alice``, ``repo:strata-bridge`` …); the replayer maps those to DB
-- primary keys via ``org.corpus_id_map`` and uses the map on every subsequent
-- event that references the same entity.
-- =============================================================================

ALTER TABLE org.runs
    ADD COLUMN IF NOT EXISTS profile_kind TEXT NOT NULL DEFAULT 'profile'
        CHECK (profile_kind IN ('profile', 'corpus')),
    ADD COLUMN IF NOT EXISTS corpus_path TEXT,
    ADD COLUMN IF NOT EXISTS replay_cursor TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS org.corpus_id_map (
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    corpus_id TEXT NOT NULL,                 -- e.g. 'person:alice', 'repo:strata-bridge'
    entity_type TEXT NOT NULL,               -- 'person'|'team'|'project'|'repo'|'channel'|...
    db_pk UUID NOT NULL,                     -- the row in org.* or app_*.*
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, corpus_id)
);

CREATE INDEX IF NOT EXISTS corpus_id_map_run_type
    ON org.corpus_id_map (run_id, entity_type);
