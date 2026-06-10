-- =============================================================================
-- app_telegram.*  — Telegram as an ingestion source (messages in dialogs:
--   private chats, basic groups, and channels/supergroups), consumed via the
--   MTProto user-account API through Telethon.
--
-- Telegram is the FIRST non-HTTP, non-AWS source: the real transport is the
-- MTProto encrypted binary protocol (Telethon `messages.getHistory` backward
-- paging + a persistent updates connection). Reproducing the binary wire is
-- infeasible (full TL/DH/AES-IGE server) and is NOT how Fyralis itself tests
-- Telegram (its synthetic mode is an in-process `MockTelegramClient`, NOT a
-- client-over-a-URL — there is NO real-wire endpoint seam for Telegram, unlike
-- AWS's `endpoint_url`). So this mock reproduces the MTProto **method contract**
-- (the surface `MockTelegramClient` + Telethon's `_message_to_dict` define) over
-- a transport substitution — HTTP for the request/response reads + a WS gateway
-- for the live push (the Discord-gateway analog the flow doc itself names). The
-- substitution is the one logged divergence; every method/object SEMANTIC is
-- faithful (verified vs core.telegram.org + the Telethon docs).
--
-- DUAL EDGE (backward-paged backfill + persistent-connection live, NO webhook):
--   - BACKFILL (pull): `messages.getHistory` pages BACKWARD on `offset_id`
--     (0 = newest; returns messages with id < offset_id, newest-first, ≤100/page;
--     next cursor = MIN id of page; bounded below by an EXCLUSIVE `min_id` floor
--     for incremental re-walks). One shard per dialog.
--   - LIVE (push): a persistent MTProto updates connection (gateway, like
--     Discord) pushes `updateNewMessage`/`updateEditMessage`. There is NO HTTP
--     webhook and NO HMAC — the trust boundary is the authenticated connection
--     (the session credential). Telegram is deliberately absent from any webhook
--     VERIFIERS map.
--
-- dedup external_id = telegram:{installation}:{dialog_id}:{message_id}:{edit_date|none}
--   (install-namespaced + edit-versioned: an edit re-observes via a fresh
--   edit_date while the message_id stays the same).
--
-- Timestamps: a message `date`/`edit_date` is EPOCH SECONDS on the MTProto wire
-- (Telethon surfaces aware datetimes; the canonical record flattens back to epoch
-- seconds). We store epoch seconds in date_ts/edit_date_ts + a tz-aware mirror in
-- created_at for the timeline join.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS app_telegram;

-- One Telegram install per run: the persisted MTProto session credential the
-- mock validates (Fyralis presets the StringSession to `spam-telegram` in spammer
-- mode), the MTProto application id/hash, and the self-account identity returned
-- by the get_me / users.getFullUser connectivity probe.
CREATE TABLE IF NOT EXISTS app_telegram.installations (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    account_label TEXT NOT NULL,             -- the install namespace (external_id segment)
    session_string TEXT NOT NULL,            -- the persisted StringSession credential (auth_key stand-in)
    api_id TEXT NOT NULL,                     -- MTProto application id (string-typed, per ADR-0003)
    api_hash TEXT NOT NULL,                   -- MTProto application hash
    self_user_id BIGINT NOT NULL,            -- get_me().id (the authenticated account)
    self_username TEXT,                       -- get_me().username (string|None)
    self_phone TEXT,                          -- get_me().phone (string|None)
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (run_id)
);

-- One dialog (conversation) per row — the per-dialog backfill home. A dialog is a
-- User (private chat), Chat (basic group), or Channel (supergroup/broadcast).
-- access_hash is required to rebuild an InputPeer for User and Channel, but is
-- NULL for a basic Chat (inputPeerChat carries chat_id only). The planner fans
-- one telegram_dialog_history shard per dialog.
CREATE TABLE IF NOT EXISTS app_telegram.dialogs (
    id UUID PRIMARY KEY,
    install_pk UUID NOT NULL REFERENCES app_telegram.installations(id) ON DELETE CASCADE,
    dialog_id BIGINT NOT NULL,               -- the entity id (user_id/chat_id/channel_id)
    dialog_kind TEXT NOT NULL CHECK (dialog_kind IN ('user', 'chat', 'channel')),
    access_hash BIGINT,                       -- NULL for basic 'chat'
    title TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (install_pk, dialog_id)
);

-- One message per row. messages.getHistory pages BACKWARD over a dialog's
-- messages ordered by message_id (descending = newest-first). message_id is the
-- per-dialog Telegram message id (the backward-walk cursor + dedup grain).
-- A `from_user_id` of NULL models a channel-broadcast / self-sent message with no
-- `from_id` (the sender is implicit; direction carried by `out`). `edit_date_ts`
-- is NULL until the message is edited (then set; the message_id stays the same →
-- the edit re-observes via the edit-versioned external_id).
CREATE TABLE IF NOT EXISTS app_telegram.messages (
    id UUID PRIMARY KEY,
    dialog_pk UUID NOT NULL REFERENCES app_telegram.dialogs(id) ON DELETE CASCADE,
    message_id BIGINT NOT NULL,              -- per-dialog Telegram message id (cursor + dedup)
    date_ts BIGINT NOT NULL,                 -- creation date, EPOCH SECONDS (MTProto wire)
    edit_date_ts BIGINT,                      -- last edit date, EPOCH SECONDS; NULL until edited
    text TEXT NOT NULL DEFAULT '',           -- the message body ('' for media-only / service)
    out BOOLEAN NOT NULL DEFAULT FALSE,       -- true for messages the self account sent
    from_user_id BIGINT,                      -- the sender's user id; NULL = channel-broadcast/self-sent (no from_id)
    created_at TIMESTAMPTZ NOT NULL,
    is_historical BOOLEAN NOT NULL DEFAULT TRUE,
    timeline_event_id UUID,
    UNIQUE (dialog_pk, message_id)
);
-- Backward walk = newest-first by message_id within a dialog. Index that order.
CREATE INDEX IF NOT EXISTS telegram_messages_dialog_id_idx
    ON app_telegram.messages (dialog_pk, message_id DESC);
