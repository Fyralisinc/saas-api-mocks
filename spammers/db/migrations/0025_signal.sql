-- =============================================================================
-- app_signal.*  — Signal as an ingestion source (conversation messages in a
--   linked account's threads: 1:1 *direct* chats + *group* threads), consumed via
--   a signal-cli linked device.
--
-- Signal is the SECOND comms gateway source (after Telegram) and is, per the
-- Fyralis flow doc, "cloned from Telegram" (ADR-0003 Topology B): a
-- linked-device messaging surface with NO OAuth, NO HTTP webhook, NO poll — just
-- a backward-paged history backfill + a persistent receive-loop gateway, both
-- converging on ONE `signal:message` handler.
--
-- TRANSPORT (the one logged divergence): Signal has NO official server API; the
-- only sound integration is **signal-cli in JSON-RPC daemon mode** (link
-- signal-cli as a secondary device, `signal-cli -a <number> daemon --tcp`, talk
-- line-delimited JSON-RPC 2.0). But signal-cli is forward-only — it has NO
-- backward history-fetch method at all (`receive`/`subscribeReceive` drain the
-- server's transient queue; the complete command list has no `get_history`), so
-- a "faithful signal-cli" mock could not serve the backward-paged backfill the
-- Fyralis contract assumes. AND Fyralis itself tests Signal via an in-process
-- `MockSignalClient` (rebinding `_open_signal_client`), NOT a client-over-a-socket
-- — every real transport bit (`_connect`, envelope mapping, `get_history`/
-- `iter_threads`/`has_history_since`, the live receive loop) is a labelled
-- `TODO(human)` stub. So this mock reproduces the VERIFIED SignalClient METHOD
-- contract (the part the flow doc §11.2 confirms is real) over a transport
-- substitution — HTTP for the reads + a WS gateway for the live receive stream
-- (the Discord/Telegram gateway analog) — carrying the REAL signal-cli envelope
-- shapes on the wire (timestamp-ms ids, sourceUuid actors, base64 groupId,
-- dataMessage vs syncMessage.sentMessage). The substitution + "real signal-cli
-- has no backfill" are the two logged divergences.
--
-- DUAL EDGE (backward-paged backfill + persistent-connection live, NO webhook):
--   - BACKFILL (pull): `get_history` pages BACKWARD on an `offset_ts` cursor
--     (0 = newest; returns messages with ts < offset_ts, newest-first, <=100/page;
--     next cursor = MIN ts of page; bounded below by an EXCLUSIVE `min_ts` floor
--     for incremental re-walks). One shard per active thread.
--   - LIVE (push): a persistent linked-device receive loop (gateway, like
--     Discord/Telegram) streams `receive` notifications carrying each incoming
--     message envelope. There is NO HTTP webhook and NO HMAC — the trust boundary
--     is the authenticated linked-device session. Signal is deliberately absent
--     from any webhook VERIFIERS map. The linked account's OWN outgoing messages
--     (out=TRUE, syncMessage.sentMessage) are skipped on the live fan-out.
--
-- dedup external_id = signal:{installation}:{thread_id}:{message_id}:none
--   (install-namespaced; the edit slot is ALWAYS `none` — Signal v1 does not
--   support edits, so messages are immutable. Contrast Telegram's edit-versioned
--   key.) The {message_id} IS the message `timestamp` in MILLISECONDS — Signal
--   has no separate integer id; a message is identified by its sender-set
--   timestamp-ms (which doubles as the id, referenced by quotes/reactions/edits).
--
-- Timestamps: a message `timestamp` is EPOCH MILLISECONDS on the signal-cli wire
-- (sender-set; it is the message identity). We store epoch-ms in ts_ms + a
-- tz-aware mirror in created_at for the timeline join.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS app_signal;

-- One Signal install per run: the persisted linked-device session credential the
-- mock validates (Fyralis presets it to `spam-signal` in spammer mode — the
-- libsignal identity/session-store stand-in; there is NO OAuth token and NO HMAC
-- secret), plus the linked account's own identity (number/uuid) returned by the
-- `me` connectivity probe.
CREATE TABLE IF NOT EXISTS app_signal.installations (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    account_label TEXT NOT NULL,             -- the install namespace (external_id segment)
    session_string TEXT NOT NULL,            -- the persisted linked-device session (auth_key stand-in)
    account_number TEXT NOT NULL,            -- the linked account's E.164 phone (+15550100001)
    account_uuid TEXT NOT NULL,              -- the linked account's Signal ACI uuid (the stable self id)
    account_username TEXT,                    -- the linked account's Signal username (string|None)
    created_at TIMESTAMPTZ NOT NULL,
    -- disabled_at is the revocation chokepoint column the flow doc names (§9): an
    -- unlinked device would set it. Fyralis wires no auto-disable (§9 TODO(human));
    -- we carry the column for fidelity but never flip it (logged divergence).
    disabled_at TIMESTAMPTZ,
    UNIQUE (run_id)
);

-- One thread (conversation) per row — the per-thread backfill home. A thread is a
-- 1:1 DIRECT chat (keyed by the other party's Signal uuid) or a GROUP thread
-- (keyed by the base64 groupId). The planner fans one signal_thread_history shard
-- per thread. thread_id is a STRING in both cases (uuid | base64 groupId) — unlike
-- Telegram's integer dialog_id.
CREATE TABLE IF NOT EXISTS app_signal.threads (
    id UUID PRIMARY KEY,
    install_pk UUID NOT NULL REFERENCES app_signal.installations(id) ON DELETE CASCADE,
    thread_id TEXT NOT NULL,                 -- contact uuid (direct) | base64 groupId (group)
    thread_kind TEXT NOT NULL CHECK (thread_kind IN ('direct', 'group')),
    thread_title TEXT,                        -- contact name (direct) | group name (group)
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (install_pk, thread_id)
);

-- One message per row. get_history pages BACKWARD over a thread's messages
-- ordered by ts_ms (descending = newest-first). ts_ms is the Signal message
-- timestamp in MILLISECONDS — the per-thread message identity (the backward-walk
-- cursor + the dedup grain; there is no separate integer id). A `sender_uuid` of
-- NULL models a message with no first-class sender for an observation: a self-sent
-- (own/outgoing, out=TRUE) message (the linked account is the implicit sender) or
-- a group-system message. `out` is TRUE for messages the linked account sent
-- (syncMessage.sentMessage). Signal v1 has no edits → messages are immutable.
CREATE TABLE IF NOT EXISTS app_signal.messages (
    id UUID PRIMARY KEY,
    thread_pk UUID NOT NULL REFERENCES app_signal.threads(id) ON DELETE CASCADE,
    ts_ms BIGINT NOT NULL,                    -- message timestamp, EPOCH MILLISECONDS (id + cursor)
    sender_uuid TEXT,                         -- sender's Signal uuid; NULL = self-sent/system (no first-class sender)
    sender_number TEXT,                       -- sender's E.164 phone (informational; NULL for self-sent)
    sender_name TEXT,                         -- sender's profile name (informational)
    body TEXT NOT NULL DEFAULT '',            -- the message body ('' for attachment-only)
    out BOOLEAN NOT NULL DEFAULT FALSE,        -- TRUE for messages the linked account sent (syncMessage.sentMessage)
    group_revision INT,                        -- group thread revision at send time (NULL for direct)
    created_at TIMESTAMPTZ NOT NULL,
    is_historical BOOLEAN NOT NULL DEFAULT TRUE,
    timeline_event_id UUID,
    UNIQUE (thread_pk, ts_ms)
);
-- Backward walk = newest-first by ts_ms within a thread. Index that order.
CREATE INDEX IF NOT EXISTS signal_messages_thread_ts_idx
    ON app_signal.messages (thread_pk, ts_ms DESC);
