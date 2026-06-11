# Spammer + Fyralis Issues Found and Applied Fixes

Date: 2026-06-11

Repos audited:

- Spammer: `/home/sarthak/spammer`
- Fyralis: `/home/sarthak/fyralis_with_spammer/fyraliscore`

Context:

- This report follows the full Alpen model-layer sweep that ran all 25 spammer mocks against Fyralis ingestion, Think, model-layer writes, post-commit, and topology.
- The main model-layer result report is `/home/sarthak/spammer/MODEL_LAYER_VS_ALPEN_REPORT.md`.
- The run artifacts are under `/home/sarthak/spammer/model_layer_run_artifacts/`.

## Executive Summary

Issues were found in both work areas, but they were different kinds of issues.

In the spammer repo, two real source-level issues needed cleanup:

- Full-corpus sweeps needed a fast mode, but the initial run-time changes disabled throttles unconditionally. That was too broad for the repo because default mock behavior should preserve API-fidelity tests.
- Replayed Gmail corpus messages stored an RFC822 id in the database but did not include a `Message-ID` header in the Gmail API payload. Fyralis Gmail ingestion requires that header, so Gmail ingestion initially failed for all Gmail records until the harness synthesized it.

In the Fyralis repo, the existing production retrieval codec fix was already present. The remaining blockers found during the sweep were mostly harness/adapter issues in the untracked Alpen run harness, not production code defects. They were applied in `/home/sarthak/fyralis_with_spammer/fyraliscore/_alpen_model_layer_run.py` so the full corpus could run through the model layer.

## Spammer Repo: Issues and Applied Fixes

### 1. Throttle removal was too broad after the model-layer run

File changed:

- `/home/sarthak/spammer/spammers/common/rate_limit.py`
- `/home/sarthak/spammer/spammers/github/ratelimit.py`
- `/home/sarthak/spammer/spammers/slack/routes/conversations.py`

Problem:

- The model-layer task correctly needed ingestion speed over mock API pacing.
- During the sweep, the common token bucket returned immediately, GitHub's limit was raised from 5,000/hour to 1,000,000/hour, and Slack history/replies page caps were relaxed.
- Those hard-coded changes were useful for the one-off sweep but unsafe as permanent defaults because they changed mock fidelity and would break tests or consumers expecting real-ish limits.

Applied fix:

- Added an opt-in sweep-mode flag: `SPAMMER_SWEEP_MODE=1`.
- Default behavior now remains faithful:
  - Common token buckets consume tokens normally.
  - GitHub defaults to `5000` requests/hour.
  - Slack non-marketplace history/replies defaults to the post-2025 cap of `15`.
- When `SPAMMER_SWEEP_MODE=1`:
  - Common token buckets keep bucket metadata but skip pacing.
  - GitHub exposes a sweep limit of `1_000_000`.
  - Slack history/replies can request up to `1000` objects/page.

Expected usage for future full-corpus model-layer sweeps:

```bash
SPAMMER_SWEEP_MODE=1 ./dev.sh serve <provider>
```

or export it once before starting all mocks:

```bash
export SPAMMER_SWEEP_MODE=1
```

Why this is better:

- Normal test/API fidelity is preserved.
- Full-corpus backfills no longer wait on synthetic quotas.
- The behavior is explicit and reversible.

### 2. Gmail replay omitted `Message-ID` from payload headers

File changed:

- `/home/sarthak/spammer/spammers/corpus/replay.py`

Problem:

- Corpus replay inserted Gmail rows with `rfc822_msg_id`, but the `headers` JSON array did not include `{"name": "Message-ID", ...}`.
- Fyralis's Gmail ingestion validates Gmail message payloads and rejects messages without a `Message-ID` header.
- In the first ingestion attempt, Gmail produced 944 record-level errors with the shape `ValidationError: gmail: Message-ID header is required`.

Applied fix:

- Imported and used `gmail_rfc822_id("alpenlabs.io")`.
- Added the generated RFC822 id to the Gmail API payload headers as `Message-ID`.
- Reused the same RFC822 id for the database `rfc822_msg_id` column.
- Kept per-mailbox Gmail API `message_id` values distinct, as the existing replay docstring intended.

Result:

- The final full sweep ingested all 944 Gmail records with zero Gmail errors.

## Fyralis Repo: Issues and Applied Fixes

### 1. Retrieval vector codec fix already present

File checked:

- `/home/sarthak/fyralis_with_spammer/fyraliscore/services/reasoning/retrieval/pathways.py`

Status:

- The expected `_ensure_vector_codec` retrieval/pathways fix was already present before the final run.
- No new production patch was needed for this item.

Why it mattered:

- Without a vector codec or fallback parsing, pgvector values can come back as string literals and fail model/observation hydration during retrieval.
- The current code already parses non-list vector values before constructing shared row types.

### 2. Alpen run harness needed exact mock-client and data-shape adapters

File changed/created:

- `/home/sarthak/fyralis_with_spammer/fyraliscore/_alpen_model_layer_run.py`

Problem:

- The full model-layer sweep had to drive all 25 spammer mocks directly through Fyralis ingestion internals.
- Several source clients and handlers needed exact local adapter behavior to match mock authentication, mock URL layout, corpus shape, or direct-ingest invocation.

Applied harness fixes:

- Set the Gmail source channel to `gmail:` so direct `ingest()` calls route through the Gmail handler.
- Added Gmail `Message-ID` normalization in the harness for the completed run. This is now fixed at the spammer replay source too.
- Added `hibob` entity types `employee`, `timeoff`, and `payroll` so all Hibob planners/fetchers could run.
- Passed GitHub webhook metadata as request headers during direct ingest so GitHub webhook validation had the headers it expects.
- Patched Jira auth token handling for the exact mock token.
- Used `expires_at_ms=None` when constructing mock AWS static credentials.
- Converted Grafana `org_id` to string for the Fyralis installation row.
- Closed Google HTTP client context correctly through `__aexit__`.
- Added a cursor spin guard to stop if a fetcher returns an empty non-terminal page with the same cursor.
- Quoted the SQL column `"natural"` in report/model queries.
- Converted phase-bound dates to timezone-aware datetimes for asyncpg timestamptz comparisons.
- Stubbed observability import needed by this isolated harness execution path.
- Added compact record-error logging so source-specific handler failures were visible without flooding the terminal.

Status:

- These changes are currently in the untracked harness file, not in production Fyralis modules.
- They are appropriate for the Alpen model-layer run harness. They should be promoted into production only after deciding which direct-ingest/harness workflows should be first-class.

### 3. Model-layer behavior issues found, not code-patched here

Source:

- `/home/sarthak/spammer/MODEL_LAYER_VS_ALPEN_REPORT.md`

These are product/model-layer findings rather than simple code defects:

- Phase-level synthesis is weak. The model layer captures local operational artifacts but often misses company-history milestones.
- Temporal framing is weak for historical scheduled events. Some 2025 events were modeled as future verification plans even though the run date was 2026-06-11.
- Financing semantics are weak. The model sees wires/invoices/payments but misses round totals, investor leads, and grant semantics.
- Aggregate current-state modeling is missing. It did not create durable beliefs for 42 people, 323 Drive files, 187 Jira bugs, 476 Jira issues, or 4,365 AWS events.

No direct code patch was applied for these in this pass because they require model-layer design work: trigger selection, summarization prompts, aggregation jobs, or temporal-normalization logic.

## Verification

Completed successfully:

```bash
.venv/bin/python -m py_compile \
  spammers/common/rate_limit.py \
  spammers/github/ratelimit.py \
  spammers/slack/routes/conversations.py \
  spammers/corpus/replay.py
```

```bash
.venv/bin/python -m py_compile _alpen_model_layer_run.py
```

Lightweight behavior check passed:

- With no `SPAMMER_SWEEP_MODE`, common token buckets enforce exhaustion, GitHub limit is `5000`, and Slack non-marketplace history caps at `15`.
- With `SPAMMER_SWEEP_MODE=1`, common token buckets do not pace, GitHub limit is `1_000_000`, and Slack history accepts large page sizes.

Focused pytest attempt:

```bash
.venv/bin/python -m pytest \
  spammers/tests/gmail/test_gmail.py \
  spammers/tests/github/test_rest.py \
  spammers/tests/fidelity/test_app_class_limits.py
```

Result:

- `4` tests passed.
- `23` tests errored at fixture setup because the local Postgres instance rejected the test fixture credentials `postgres/postgres`.
- The failures were environment/authentication errors, not assertion failures from the patch.

Representative error:

```text
asyncpg.exceptions.InvalidPasswordError: password authentication failed for user "postgres"
```

The prior full Alpen run verification remains valid:

- All 25 sources ingested with zero source errors.
- `47,747` observations total.
- Think completed with `126` processed attempts and `0` pending Signal T1 triggers.
- Post-commit processed `153`, failed `0`, dead-lettered `0`.
- Topology processed `125`, completed `125`, failed `0`.

## Current Worktree Impact

Spammer tracked files changed:

- `spammers/common/rate_limit.py`
- `spammers/github/ratelimit.py`
- `spammers/slack/routes/conversations.py`
- `spammers/corpus/replay.py`

Spammer generated/untracked outputs:

- `MODEL_LAYER_VS_ALPEN_REPORT.md`
- `BOTH_REPOS_ISSUES_REPORT.md`
- `model_layer_run_artifacts/`

Fyralis untracked harness:

- `_alpen_model_layer_run.py`

Other untracked Fyralis files were already present in the worktree and were not modified as part of this pass.

## Recommendation

Keep the spammer source patches. They fix a real Gmail mock fidelity gap and turn sweep acceleration into an explicit mode without harming normal mock behavior.

Keep `_alpen_model_layer_run.py` as the reproducible harness for the Alpen model-layer run. If this workflow is going to be repeated, promote the useful parts into a supported Fyralis integration-test or benchmark command rather than leaving them as a one-off script.

For the model layer itself, the next useful fixes are not in the mock clients. They should target company-phase synthesis, historical-event temporal normalization, financing/grant semantics, and aggregate current-state belief generation.
