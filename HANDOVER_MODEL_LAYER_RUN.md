# HANDOVER — Full Alpen ingestion → model layer → per-phase ground-truth report

You are a fresh instance picking up a long-running effort. This doc is **self-contained** —
read it fully before acting. Prior context is in `~/spammer/INGESTION_SWEEP_FINDINGS.md` and
`~/spammer/FYRALIS_PLACEHOLDER_INGESTION_CLIENTS.md` (read both; they explain the system and
what's broken).

---

## 0. Mission (what the user wants)

1. Run **all 25 spammer mocks with the COMPLETE corpus data** (not the ≤100 trimmed set).
2. Point **Fyralis** at the spammers and run the **full pipeline: ingestion → think → model
   layer → everything** (post-commit, topology/patterns).
3. **Fix issues as they arise and keep going until the whole pipeline completes.**
4. The goal is **the model layer, NOT ingestion fidelity.** So **remove rate limits and
   anything that slows ingestion** — make ingestion fast; don't optimize ingestion accuracy.
5. Produce a **very detailed report** on the model layer: every model created, every pattern
   found, everything the model layer developed (size is not a concern — make it exhaustive).
6. **Report layout = per phase of Alpen's company history:** for each phase, lay out the
   REAL data / patterns / company+worker state (from the corpus ground truth) and compare it
   against the model layer's beliefs for that phase. Call out: expected-vs-observed deviations,
   which model beliefs are correct, which important patterns/findings the model layer MISSED,
   etc. Net: **how correct is the model layer's picture of Alpen vs reality.**

---

## 1. Environment map

| Thing | Location / value |
|---|---|
| Spammer repo (25 mocks + corpus) | `~/spammer` (Postgres DB `mock_orgs`) |
| Fyralis ("Company OS") backend | `~/fyralis_with_spammer/fyraliscore` (Postgres DB `company_os`) |
| Infra (already running in Docker) | `company_os_postgres` :5432, `company_os_ollama` :11434, `fyralis_dev_kafka` :9092, `fyralis_dev_moto_s3` :5001 |
| Postgres creds | `company_os:company_os@localhost:5432` (DBs: `company_os`, `mock_orgs`) |
| Fyralis venv | `~/fyralis_with_spammer/fyraliscore/.venv` |
| Spammer venv + runner | `~/spammer/.venv`; `~/spammer/dev.sh` (setup/prepare/serve/status/reset) |
| **LLM (WORKING)** | **codex / gpt-5.5 via ChatGPT app-server** — `~/.codex/auth.json` (user keeps it logged in). `codex` CLI at `~/.local/bin/codex`. |

**LLM env (use this — it works, capable + fast, no API key/billing):**
```bash
export PATH="$HOME/.local/bin:$PATH"
export LLM_PROVIDER=codex CODEX_TRANSPORT=app-server LLM_MODEL=gpt-5.5
export LLM_TIMEOUT_SECONDS=300
```
DeepSeek (the .env default) is **out of balance (HTTP 402)**; Anthropic/OpenAI keys are empty;
local Ollama models are too weak (7b) or too slow on CPU (14b). **Use codex.** If codex auth
ever 401s, the user must `codex login` again (tokens expire; access ~10-day, refresh single-use).
Always sanity-check codex first: build the provider and make one call (expect a clean reply;
ignore a harmless "Event loop is closed" subprocess-cleanup line at process exit).

`MASTER_KEK` (for the encrypted secret store) lives in `~/fyralis_with_spammer/fyraliscore/.env`:
`export MASTER_KEK="$(grep '^MASTER_KEK=' .env|cut -d= -f2-)"`.

---

## 2. What's already established (don't rediscover)

### 2.1 Bugs found + fixed (model layer was broken; now works)
- **pgvector codec crash (FIXED)** — `services/reasoning/retrieval/pathways.py` bound a
  stringified `'[…]'::vector` while the codec was live → "could not convert string to float"
  → crashed every model write → **0 models**. Fixed (ensure codec + numpy).
- **Mercury 30-day backfill truncation (FIXED)** — `services/ingest/ingestion/fetchers/mercury.py`.
- **`modality` CheckViolation** — already fixed on `main` (`sanitize_explicit_grammar_axes`).
- Both our fixes are committed on branch **`fix/model-layer-pgvector-codec-and-mercury-backfill`**
  (commit `32322473`, on top of `main` `bc729693`). **NOTE: this branch is NOT pushed and NOT
  merged. Make sure you're on it (or rebase it onto current main) before running, or the codec
  crash returns and you get 0 models.** Check: `git -C ~/fyralis_with_spammer/fyraliscore branch --show-current`.

### 2.2 Ingestion reality — REAL vs PLACEHOLDER clients
Historically Fyralis had **9 placeholder clients that couldn't ingest** (brex, deel, ramp,
gusto, carta, linkedin, figma, fireflies, hibob — wrong API paths → 404; detail in
`FYRALIS_PLACEHOLDER_INGESTION_CLIENTS.md`). **The user says the Fyralis team will fix these
before this run.** So treat all 25 sources as expected-to-work; **if any source still 404s /
errors during ingestion, debug and solve it as it comes** (cross-check the real API contract
in the placeholder report + official docs — the spammer mock is the faithful reference; if a
client diverges, the client is wrong, not the mock). The human-signal sources
(slack/github/gmail/discord/notion/jira/calendar/drive) carry the org/people/project graph
that matters most for the model layer; the finance/HR/equity ones add comp/spend/cap-table.

### 2.3 Token convention (for the OAuth sources)
The spammer is a **corpus run** that stores REAL-format tokens (`xoxb-…`, github/discord/notion
bot tokens) and validates against them. Fyralis's spammer-mode (`SYNTHETIC_SOURCE_API_BASE`)
presets `spam-*` tokens which the corpus run **rejects**. So for slack/discord/github/notion you
must hand the client the **real stored token** from `mock_orgs` (e.g.
`select bot_token from app_slack.workspaces`). Mercury and other any-token sources work directly.
(See the working `_proof_slack_spammer.py` harness which does exactly this for slack.)

### 2.4 Working in-process harnesses (your starting templates)
In `~/fyralis_with_spammer/fyraliscore/`:
- `_proof_mercury_spammer.py` — mercury: real fetcher → `ingest()` → observations → Think → models. **Proven.**
- `_proof_slack_spammer.py` — slack with real bot token + pacing. **Proven.**
- `_connsweep.py`, `_sweep_finance.py` — connectivity/triage helpers.
These show the pattern: build the source client (spammer mode + real token where needed) →
drive its planner/fetcher → `ingest("<source>:<kind>", record, pool, tenant_id)` →
`run_signal_t1_triggers_until_complete(...)` (Think drain) → query `models`.

### 2.5 Model-layer behavior observed
- Produces `models` (kind ∈ belief/observation/prediction/norm; claim_role ∈
  fact/situation/concern/recommendation/prediction) + `model_edges` (relationships).
- **Key limitation:** the model layer is only as rich as the cross-source **org graph**. A
  single-source run yields correct-but-under-scoped models ("the external sender", "commitment
  not identified"). Ingesting MANY sources into ONE tenant is what gives rich, attributed
  models — this is exactly why the user now wants the FULL multi-source run.
- Patterns/relationships come from the **post-commit + topology/SAGE** workers — run them, not
  just Think, or you'll miss the "patterns found".

---

## 3. Execution plan

### Step A — Full-data spammers
**The corpus + seeders were just remediated by the user (2026-06-11)** — timeline now runs
through **Jun 2026** (Mosaic launch, BTC Credit Markets, more hires; headcount 42), Drive
323 files, Jira 187 bugs/476 issues, comms adoption dates fixed, AWS/GitHub realism restored.
**Read `~/spammer/ALPEN_COMPANY_STATE_REPORT.md` — it is the authoritative current Alpen state
and its month-by-month table is your per-phase ground-truth spine for the report.**

Re-prepare with the new data, anchored at the remediated `virtual_now`:
```bash
cd ~/spammer
./dev.sh reset --confirm=yes                      # drops mock schemas (reset list now includes
                                                  # aws/telegram/signal — fix already in cli.py)
AS_OF=2026-06-11 ./dev.sh prepare                 # full corpus through Jun-2026 + all seeds
# (the events.jsonl was re-rendered; this anchors the company "now" at 2026-06-11.)
```
This creates one run with the full data (slack ~10k+ msgs, github ~12k, Drive 323, etc.). Then
start all 25 mocks (ports 7001–7025):
```bash
# launch each: python -m spammers.<provider> run --port <port>   (see dev.sh cmd_serve for the
# port map). A loop over the 25 providers backgrounding each is fine. Health: GET /_health.
```
`~/spammer/dev.sh` has the provider→port map (slack 7001 … signal 7025) and a `serve` subcommand.

### Step B — Remove rate limits (for speed)
The mocks enforce real rate limits (e.g. slack 429 — it throttled hard in testing). Disable
globally for this run: the shared limiter is `~/spammer/spammers/common/rate_limit.py`
(`RateLimiter.take()`); patch it to always-allow (return ok/no-retry) — that disables RL across
all sources. Per-source middleware (e.g. `spammers/slack/ratelimit.py`) calls `take()`, so one
patch covers them. Also do **not** arm the `POST /_control/rate_limit?count=N` test endpoints.
Restart the mocks after patching.

### Step C — Ingest all WORKING sources into ONE unified Alpen tenant
The model layer needs all sources under **one tenant** to build the org graph. Two routes:

- **Route 1 (recommended, proven): in-process multi-source ingest.** Extend the
  `_proof_*_spammer.py` pattern to every working source, all writing into ONE DB + ONE
  `tenant_id` (use `create_gateway_pool(dsn)` so the global pool has the codec). For each
  source: build its client (real token for OAuth sources), drive its planner+fetcher
  (`PLANNER_DISPATCH`/`FETCHER_DISPATCH` in `services/ingest/ingestion/`), call
  `ingest("<source>:<kind>", record, pool, tenant_id)`. You already have mercury + slack done;
  add github, gmail, discord, notion, jira, calendar, drive, ashby, miro, quickbooks, grafana,
  aws. Per-source onboarding/record shapes differ — `scripts/sandbox_<source>.py` and the
  `services/ingest/integrations/<source>/` modules are your reference.
- **Route 2 (faithful but heavy): production worker stack.** The ingest workers are
  "production"-mode (`services/platform/runtime/process_manifest.py`): oauth_poller,
  tenant_onboarding, source_onboarding, shard_fetch, reconciler, normalizer, observation_writer
  (+ embedding_worker), driven through Kafka + moto-S3 (both already up). You'd seed a
  `provider_installations` row per source pointing at the spammer + emit an `onboarding_trigger`,
  and the workers fan out. More faithful, but more moving parts and the placeholder sources
  still fail. `dogfood_up.sh` only starts the gateway + reasoning workers — NOT the ingest
  workers — so you'd launch the ingest workers from the manifest commands yourself.

Either way, verify observations: `select source_channel, count(*) from observations where
tenant_id=$1 group by 1;`. **Goal: fast + broad, not perfect.** Cap per-source volume if think
cost balloons (see Step D).

### Step D — Run Think + post-commit + topology over the observations
- Drain Think (the model layer) with codex over the ingested observations. The reusable drain
  is `run_signal_t1_triggers_until_complete(tenant_id, pool=..., provider=_build_cached_provider(),
  observation_ids=[...], timeout_seconds=...)` (from `scripts/run_100_signal_real_llm_e2e.py`).
  Then `drain_post_commit_actions(pool, tenant_id=...)` and the **topology/SAGE** workers for
  patterns/edges (`scripts/run_topology_sweeper.py`, `run_sage_*`).
- **SCALE WARNING:** Think is ~1 codex call per observation (plus follow-ups). The full corpus
  is **thousands** of observations → thousands of codex calls → hours + real usage. Be
  deliberate: either (a) run the full set and let it churn (checkpoint progress; it's
  resumable by observation_id), or (b) sample representatively **per phase** (see §4) so every
  company phase is covered — this directly serves the report and bounds cost. Recommend (b)
  unless the user wants literally-everything. Watch for `out_of_region` (soft, normal) and any
  new `*_valid` CheckViolations (coerce like the modality fix did).

### Step E — The per-phase comparison report (the deliverable)
Compare the model layer's beliefs **per company phase** against Alpen ground truth. Structure:
for each phase → (1) real data/state (from the corpus) → (2) model-layer beliefs/patterns for
that window → (3) correct ✓ / deviated ✗ / **missed** (important real things the model layer
didn't infer). End with an overall correctness assessment.

---

## 4. Ground truth — Alpen's phases (for the report's spine)

**START HERE: `~/spammer/ALPEN_COMPANY_STATE_REPORT.md`** is the authoritative, current
company-state report (generated from the live `mock_orgs` DB after the 2026-06-11 remediation).
Its **§3 month-by-month breakdown** (HC, raised, burn, cash, commits, PRs, Slack, Jira,
incidents, "what's happening" per month, Feb-2024 → Jun-2026) plus §4 people patterns, §5
funding, §6 per-source fidelity ARE your per-phase ground truth. For each phase, take that
report's real numbers/events/state and compare the model layer's beliefs against them.

Alpen Labs (Bitcoin financial system; BitVM/Strata). Founded **2024-02-01**. The corpus is a
high-fidelity simulation grounded in real public Alpen data. Deeper ground-truth in `~/spammer/corpus/`:
- `corpus/facts/facts.yaml` — company, people, products (Strata, Glock, Mosaic, Strata Bridge,
  BTC Credit Markets, Bitcoin Dollar), `milestones_curated` (DATED phases — your phase spine).
- `corpus/facts/people.enriched.yaml` — the ~38 people (real handles/teams: bridge, protocol).
- `corpus/facts/{finance,office_life,chatter}.yaml`, `corpus/threads/THR-0xx-*.yaml` — 13 dated
  initiative arcs (the "phases"): convergence/launch, strategic-round, alpen-testnet,
  glock-is-here, starknet-shared-glock, prague-testnet, mosaic, alpen, strata-bridge, mosaic,
  alpen-dashboards, bitcoind-async-client, zkaleido.
- `corpus/build/events.jsonl` — the full replay log (every event with a timestamp `t`).

**Key dated milestones (phase boundaries), e.g.:** 2024-02 founded · 2024-04-09 public launch /
first blog · 2025-01-09 strategic funding round · 2025-08-04 public testnet live · 2025-08-19 …
plus the Glock/Mosaic/Strata-Bridge/zkaleido initiative arcs. Use `milestones_curated` +
`corpus/threads/` to define the phases and extract each phase's real people/work/decisions/state.

---

## 5. Querying the model layer (company_os / your tenant)

```sql
-- models (beliefs) by kind/claim_role
select proposition->>'kind' kind, claim_role, count(*) from models where tenant_id=$1 group by 1,2;
-- the actual beliefs, time-windowed (occurred/scope_temporal → map to a phase)
select "natural", confidence, proposition, scope_temporal, created_at from models where tenant_id=$1 order by created_at;
-- relationships
select kind, count(*) from model_edges where tenant_id=$1 group by 1;      -- NB column is `kind`, not in all schemas; check \d model_edges
-- actors/entities the model layer resolved (people/projects) — richer graph = better attribution
select * from actors where tenant_id=$1;     select * from entity_aliases where tenant_id=$1;
```
Patterns/relationships also live in the topology/SAGE outputs — inspect the schema for
`pattern_*`, `sage_*`, latent-relationship tables (`\dt` + grep migrations) after running those
workers. The `models.scope_temporal` / `occurred_at` fields let you bucket beliefs into phases.

---

## 6. Critical gotchas (will bite you)

1. **Be on the fix branch** (or cherry-pick `pathways.py`) — else the codec crash → **0 models**.
2. **Use `create_gateway_pool(dsn)`** for any harness pool — it registers the pgvector codec AND
   sets the global `lib.shared.db._pool` (so Think sub-components that call `get_pool()` get the
   codec). Raw `asyncpg.create_pool(init=_register_codecs)` is NOT enough on its own.
3. **codex** is the only working LLM. Verify it before a long run. Harmless "Event loop is
   closed" at exit ≠ failure.
4. **OAuth sources need the real stored token** (slack/discord/github/notion) — spammer-mode
   `spam-*` tokens are rejected by the corpus run. See `_proof_slack_spammer.py`.
5. **Placeholder sources (brex/deel/ramp/gusto/carta/linkedin/figma/fireflies/hibob) 404** —
   skip or rewrite. Don't burn time debugging "wiring"; the clients are wrong (see the report).
6. **Disable rate limits** (Step B) or slack/others will 429 and you'll only get one channel.
7. **`ensure_partitions(pool, as_of=date(2024,1,1), months_ahead=36)`** before ingest — the
   `observations` table is range-partitioned by `occurred_at`; corpus data spans 2024–2026, so
   create back-partitions or inserts fail.
8. **Scale/cost:** thousands of observations × codex = hours + usage. Phase-sample if needed.
9. **CheckViolations:** if Think throws `*_valid` CheckViolationErrors for other grammar axes,
   coerce them the way `main` did for modality (`sanitize_explicit_grammar_axes` in
   `lib/shared/memory_grammar.py` is the pattern).

---

## 7. Suggested order of operations
1. Confirm git branch (fix branch) + codex works + infra up.
2. Re-prepare full spammer data + start 25 mocks + disable rate limits.
3. Ingest the working sources into one tenant (start with slack+github+gmail+jira+notion —
   the people/project signal — then add the rest). Verify observation counts per source.
4. Run Think (phase-sampled or full) + post-commit + topology.
5. Snapshot the model layer (models/edges/patterns/actors) and bucket beliefs by phase.
6. Write the per-phase comparison report (corpus ground truth vs model beliefs; correct/deviated/missed).

Deliverable: an exhaustive report at e.g. `~/spammer/MODEL_LAYER_VS_ALPEN_REPORT.md`.

---
*Prior findings: `~/spammer/INGESTION_SWEEP_FINDINGS.md`,
`~/spammer/FYRALIS_PLACEHOLDER_INGESTION_CLIENTS.md`. Fix branch:
`fix/model-layer-pgvector-codec-and-mercury-backfill` @ `32322473` (unpushed).*
