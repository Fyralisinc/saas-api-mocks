-- =============================================================================
-- app_calendar.*  — Google Calendar projection state
--
-- One account per run (the Google Workspace customer + service account the
-- consumer impersonates via domain-wide delegation), one calendar per person
-- (calendar_id == the person's primary calendar == their email), and one row
-- per event. ``updated_at`` is the high-water mark incremental sync + the
-- reconcile probe (updatedMin) read against; ``status`` flips to 'cancelled'
-- for deletions (which still surface under showDeleted=true).
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS app_calendar;

CREATE TABLE IF NOT EXISTS app_calendar.accounts (
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

CREATE TABLE IF NOT EXISTS app_calendar.calendars (
    id UUID PRIMARY KEY,
    account_pk UUID NOT NULL REFERENCES app_calendar.accounts(id) ON DELETE CASCADE,
    person_id UUID NOT NULL REFERENCES org.people(id),
    calendar_id TEXT NOT NULL,             -- == person email == "primary"
    summary TEXT NOT NULL,
    time_zone TEXT NOT NULL DEFAULT 'UTC',
    UNIQUE (account_pk, calendar_id)
);

CREATE TABLE IF NOT EXISTS app_calendar.events (
    id UUID PRIMARY KEY,
    calendar_pk UUID NOT NULL REFERENCES app_calendar.calendars(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL,                -- Google opaque event id (base32hex-ish)
    status TEXT NOT NULL DEFAULT 'confirmed',   -- 'confirmed' | 'tentative' | 'cancelled'
    summary TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    start_time TIMESTAMPTZ,
    end_time TIMESTAMPTZ,
    all_day BOOLEAN NOT NULL DEFAULT FALSE,
    organizer_email TEXT,
    creator_email TEXT,
    attendees JSONB NOT NULL DEFAULT '[]'::jsonb,
    recurring_event_id TEXT,
    event_type TEXT NOT NULL DEFAULT 'default',
    hangout_link TEXT,
    html_link TEXT,
    sequence INTEGER NOT NULL DEFAULT 0,
    ical_uid TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,       -- high-water for syncToken + updatedMin
    timeline_event_id UUID REFERENCES timeline.events(id),
    UNIQUE (calendar_pk, event_id)
);
CREATE INDEX IF NOT EXISTS calendar_events_cal_updated_idx
    ON app_calendar.events(calendar_pk, updated_at);
CREATE INDEX IF NOT EXISTS calendar_events_cal_start_idx
    ON app_calendar.events(calendar_pk, start_time);
