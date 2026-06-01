-- =============================================================================
-- 0009_webhook_retry.sql
--
-- Real webhook senders (Slack Events API, GitHub, Gmail Pub/Sub) RETRY a
-- delivery that gets a non-2xx response or times out — over a long window
-- (Slack ~3×/hour, GitHub ~8×/hour) — rather than dropping it after one burst.
-- The mock previously stamped ``emitted_at`` regardless of the delivery status,
-- so any failure that outlasted the in-call retry budget (e.g. a receiver that
-- is briefly saturated) was silently lost.
--
-- These columns let ``mark_emitted`` keep a failed event PENDING and reschedule
-- it: the EmissionLoop re-attempts once ``emit_next_attempt_at`` passes, until a
-- bounded number of attempts, after which it is dead-lettered (stamped + logged).
-- =============================================================================

ALTER TABLE timeline.events
    ADD COLUMN IF NOT EXISTS emit_attempts INTEGER NOT NULL DEFAULT 0;

ALTER TABLE timeline.events
    ADD COLUMN IF NOT EXISTS emit_next_attempt_at TIMESTAMPTZ;  -- real-time gate for the next retry
