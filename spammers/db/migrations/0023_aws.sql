-- =============================================================================
-- app_aws.*  — AWS as an ingestion source (CloudTrail management events +
--   CloudWatch alarm-state changes), accessed via the IAM-SigV4-signed
--   CloudTrail LookupEvents API + STS GetCallerIdentity / AssumeRole.
--
-- Unlike every other mock, AWS speaks the GENUINE AWS wire protocols — botocore
-- talks to this server via an `endpoint_url` override (the moto/localstack seam
-- Fyralis documents): CloudTrail uses the AWS JSON 1.1 protocol (POST /, header
-- `X-Amz-Target: com.amazonaws.cloudtrail.v20131101.CloudTrail_20131101.LookupEvents`)
-- and STS uses the AWS Query protocol (form-encoded `Action=…`, XML response).
-- Every request is SigV4-signed; the mock RECOMPUTES the signature against a
-- seeded secret-access-key and rejects a mismatch with 403 SignatureDoesNotMatch
-- (the AWS analog of the other sources' webhook-tamper check).
--
-- The install row mirrors the SHAPE of grafana_installations (the time-window-
-- backfill archetype) keyed on (run, account, region) with NO per-resource child
-- table — CloudTrail management events + alarm-state changes are account/region-
-- wide, so ONE shard per install streams the events over a TIME WINDOW. It also
-- carries the seeded IAM credential the SigV4 verifier checks against (access key
-- id + secret) and the assume-role descriptor.
--
-- DUAL EDGE (time-window backfill + POLL live, NOT a webhook):
--   - BACKFILL/POLL (pull): CloudTrail:LookupEvents over a window bounded below by
--     a 90-day floor (CloudTrail's management-event retention). Pagination is an
--     opaque NextToken; newest-first; end-of-data = a page with no token.
--   - LIVE (push): there is NO webhook and NO HMAC. The live edge is a POLL — new
--     CloudTrail events accrue (orggen.live.inject_aws_event inserts a fresh
--     event row) and the consumer re-walks LookupEvents incrementally (StartTime =
--     high-water + 1ms), exactly like the reconciler. AWS is deliberately absent
--     from any webhook VERIFIERS map; the trust boundary is the IAM-authed poll.
--
-- Event timestamps: the LookupEvents wire returns `EventTime` as EPOCH SECONDS
-- (a JSON number) but we store epoch MILLISECONDS (the value the window walk pages
-- on); the native CloudTrail record inside `CloudTrailEvent` carries `eventTime`
-- as an RFC3339 `YYYY-MM-DDTHH:MM:SSZ` string. Both are derived from event_time_ms.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS app_aws;

-- One AWS install per run: a target (account_id, region) + the seeded IAM
-- credential the SigV4 verifier resolves by access_key_id, plus the assume-role
-- descriptor for the STS:AssumeRole path.
CREATE TABLE IF NOT EXISTS app_aws.installations (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    account_id TEXT NOT NULL,               -- the 12-digit AWS account id
    region TEXT NOT NULL,                    -- e.g. us-east-1 (part of external_id namespace)
    endpoint_host TEXT NOT NULL,             -- the host botocore is pointed at (mock seam)
    -- The seeded long-lived IAM access-key pair (credential_kind=static_keys). The
    -- SigV4 verifier looks the secret up by the access_key_id parsed out of the
    -- request's Credential= scope. Never the real AWS — a deterministic mock pair.
    access_key_id TEXT NOT NULL,             -- AKIA… (the seeded key the slice presents)
    secret_access_key TEXT NOT NULL,         -- the secret SigV4 is recomputed against
    -- The cross-account role + external id for the STS:AssumeRole path (the
    -- recommended credential_kind). AssumeRole mints short-lived ASIA… creds.
    role_arn TEXT NOT NULL,
    external_id TEXT,
    iam_user_arn TEXT NOT NULL,              -- GetCallerIdentity Arn for static creds
    user_id TEXT NOT NULL,                    -- GetCallerIdentity UserId (AIDA…)
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (run_id)
);

CREATE INDEX IF NOT EXISTS aws_installations_account_region_idx
    ON app_aws.installations (account_id, region);

-- One CloudTrail event per row. The LookupEvents response Event wrapper
-- (EventId/EventName/ReadOnly/EventTime/EventSource/Username/Resources/
-- CloudTrailEvent) is projected from these columns; the full native CloudTrail
-- record is stored verbatim in `record` (json.dumps → the `CloudTrailEvent`
-- string). A management event has is_alarm FALSE and no alarm_* in the record; an
-- alarm-state-change event has is_alarm TRUE and the record carries top-level
-- alarmName/newState/prevState (the discriminator the consumer keys on).
CREATE TABLE IF NOT EXISTS app_aws.events (
    id UUID PRIMARY KEY,
    install_pk UUID NOT NULL REFERENCES app_aws.installations(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL,                  -- CloudTrail eventID (GUID, immutable dedup PK)
    event_time_ms BIGINT NOT NULL,           -- epoch ms — what the window walk pages on
    event_name TEXT NOT NULL,                -- e.g. RunInstances / ConsoleLogin / SetAlarmState
    event_source TEXT NOT NULL,              -- e.g. ec2.amazonaws.com
    aws_region TEXT NOT NULL,
    username TEXT NOT NULL DEFAULT '',        -- LookupEvents Event.Username (may be '')
    access_key_id TEXT NOT NULL DEFAULT '',   -- LookupEvents Event.AccessKeyId (may be '')
    read_only BOOLEAN NOT NULL DEFAULT FALSE, -- Event.ReadOnly (wire is the STRING "true"/"false")
    resources JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{ResourceType, ResourceName}]
    record JSONB NOT NULL,                    -- the full native CloudTrail record (→ CloudTrailEvent)
    is_alarm BOOLEAN NOT NULL DEFAULT FALSE,  -- alarm-state-change vs plain management event
    created_at TIMESTAMPTZ NOT NULL,
    is_historical BOOLEAN NOT NULL DEFAULT TRUE,
    timeline_event_id UUID,
    UNIQUE (install_pk, event_id)
);
-- Reads are a backward time-window walk: newest-first by (event_time_ms, event_id)
-- within a [StartTime, EndTime] window. Index that ordering.
CREATE INDEX IF NOT EXISTS aws_events_install_time_idx
    ON app_aws.events (install_pk, event_time_ms DESC, event_id DESC);
