-- =============================================================================
-- 0008_slack_two_token.sql
--
-- Brings the Slack mock up to the NEW Fyralis ingestion architecture: the
-- two-token model (bot xoxb for channels, per-user xoxp for DMs/group-DMs) and
-- the message/thread fields real Slack returns.
--
--   * app_slack.user_tokens   — the per-user xoxp consent rows. A user token
--                               authenticates AS a human; conversations.list
--                               (types=im,mpim) + history under it read only
--                               that user's DMs. Real Slack forbids a bot token
--                               from reading human-human DMs.
--   * workspaces.app_distribution — Marketplace vs non-Marketplace. Post
--                               2025-05-29 Slack caps non-Marketplace apps to
--                               1 req/min + limit<=15 on conversations.history
--                               /replies; Marketplace/internal keep Tier 3.
--   * oauth.codes.user_scope / authed_user_id — the user scopes requested at
--                               /authorize and the consenting human, so the
--                               exchange can mint a correctly-scoped xoxp token.
--   * app_slack.messages thread/identity columns real Slack returns on parents
--                               (reply_users, reply_users_count, latest_reply)
--                               and on human messages (client_msg_id).
-- =============================================================================

-- --- app class (rate-limit / page-size behaviour) ---------------------------
ALTER TABLE app_slack.workspaces
    ADD COLUMN IF NOT EXISTS app_distribution TEXT NOT NULL DEFAULT 'non_marketplace';

ALTER TABLE app_slack.workspaces
    DROP CONSTRAINT IF EXISTS workspaces_app_distribution_check;
ALTER TABLE app_slack.workspaces
    ADD CONSTRAINT workspaces_app_distribution_check
    CHECK (app_distribution IN ('marketplace', 'non_marketplace'));

-- --- per-user xoxp user tokens (DM consent rows) ----------------------------
CREATE TABLE IF NOT EXISTS app_slack.user_tokens (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES app_slack.workspaces(id) ON DELETE CASCADE,
    slack_user_id TEXT NOT NULL,            -- the consenting human 'U…'
    user_token TEXT NOT NULL,               -- 'xoxp-…'
    scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, slack_user_id),
    UNIQUE (user_token)
);

-- --- oauth code carries the requested scopes + consenting user -------------
ALTER TABLE oauth.codes
    ADD COLUMN IF NOT EXISTS scope TEXT;          -- requested bot scopes
ALTER TABLE oauth.codes
    ADD COLUMN IF NOT EXISTS user_scope TEXT;     -- requested user scopes
ALTER TABLE oauth.codes
    ADD COLUMN IF NOT EXISTS authed_user_id TEXT; -- the consenting human 'U…'

-- --- bot membership: a bot must be invited to read a non-DM channel ---------
-- The spammer's premise is that the app ingests the channels it was installed
-- into, so this defaults TRUE; set it FALSE to model a channel the bot has not
-- joined (conversations.history then returns not_in_channel, as real Slack).
ALTER TABLE app_slack.channels
    ADD COLUMN IF NOT EXISTS bot_is_member BOOLEAN NOT NULL DEFAULT TRUE;

-- --- message thread/identity fields real Slack returns ----------------------
ALTER TABLE app_slack.messages
    ADD COLUMN IF NOT EXISTS client_msg_id TEXT;
ALTER TABLE app_slack.messages
    ADD COLUMN IF NOT EXISTS reply_users_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE app_slack.messages
    ADD COLUMN IF NOT EXISTS latest_reply TEXT;
ALTER TABLE app_slack.messages
    ADD COLUMN IF NOT EXISTS reply_users JSONB NOT NULL DEFAULT '[]'::jsonb;
