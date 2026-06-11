# Fyralis ingestion + model-layer sweep vs spammer (Alpen Labs corpus) — findings

Method: point Fyralis's REAL source clients/fetchers at the running spammer mocks
(:7001–7025, faithful to real APIs), into a throwaway DB; compare observed counts to
spammer ground truth. Divergences verified against **official API docs**. Spammer run:
corpus 6fb983d0, virtual_now 2026-01-01.

Legend: WORKS = client speaks the real API, pulls Alpen data. PARTIAL = connects but
count diverges. FAILS(placeholder) = Fyralis client uses non-real paths/contract (a
Mercury/QBO clone) → 404 against the faithful spammer → cannot ingest from the real API.

## A. INGESTION TRIAGE

### WORKS (real Fyralis clients — pull genuine Alpen data)
- **quickbooks** ✓ — `company_info` → CompanyInfo "Alpen Labs". Real QBO client.
- **grafana** ✓ — `get_org` → {id:1, name:"Alpen Labs"}. Real.
- **ashby** ✓ — `list_entities("job")` → 9 real job reqs (Protocol Engineer, Research
  Scientist…) w/ real hiring teams (Storopoli, Chhetri, Zdanovich…). Real.
- **miro** ✓ — `list_boards` → 6 boards (Q3 Roadmap, System Architecture…) w/ real
  people (Del Bonis, Gyawali, Khambhati…). Real.
- **slack** ✓ — WORKS only with the **real stored `xoxb-…` bot_token** (the spammer
  validates against app_slack.workspaces.bot_token, NOT Fyralis's `spam-slack::<team>`
  preset). `conversations_list` → 14 Alpen channels. Real client; spammer-mode token
  shortcut is incompatible with the corpus run (which uses real tokens).

### PARTIAL — real, doc-verified bug
- **mercury** — observed 58 (55 txns + 3 snapshots) vs ground truth 878 txns/3 accts.
  Cold backfill omits `start=` (planner window_start=None; fetcher only sets start on
  warm/incremental — fetchers/mercury.py:158). Real Mercury `GET /account/{id}/transactions`
  **defaults `start` to 30 days ago** when omitted (docs.mercury.com/reference/listaccounttransactions),
  so Fyralis silently backfills only the last ~30 days (~7% of history). **REAL BUG,
  reproduces against production Mercury.**

### FAILS — placeholder clients (wrong paths/contract; can't ingest from real API)
- **brex** — `GET /accounts`; real Brex is `GET /v2/accounts/cash` (developer.brex.com,
  scope accounts.cash.readonly) → 404. Mercury-clone placeholder. **Doc-verified.**
- **deel** — `GET /contracts`; real Deel is `GET /rest/v2/contracts` (api.letsdeel.com)
  → enumerate 0 / 404. Placeholder.
- **ramp** — 404 "business not found". QBO-OAuth-clone placeholder.
- **gusto** — 404 "company not found". QBO-SQL-query-clone placeholder.
- **figma** — 404 "file/resource not found". Brex-clone placeholder.
- **fireflies** — 404 "transcript not found". Fake-Brex-REST-clone placeholder.
- (carta, linkedin, hibob — placeholders per prior audits; not re-confirmed this pass.)

### NEEDS-REAL-TOKEN (real clients; spammer-mode `spam-*` preset rejected by corpus run)
- **slack** ✓ and **discord** ✓ CONFIRMED working once handed the real stored token
  (slack→14 channels, discord→5 channels). github (401 Bad credentials on `spam-gh::`)
  and notion (401) are also real clients but the corpus-run spammer validates the real
  stored token via a different auth path; they'd work post-OAuth with real tokens.

### META-FINDING — spammer-mode vs corpus run token convention
The running spammer is a **corpus run** that stores REAL-format tokens (`xoxb-…`, github
App tokens, discord/notion bot tokens) and validates against them. Fyralis's spammer-mode
seam (`SYNTHETIC_SOURCE_API_BASE`) presets `spam-slack::<team>` / `spam-gh::<inst>` /
`spam-bot::<guild>` / `spam-notion::<ws>` tokens — the **X3 harness** convention — which
the corpus run does NOT honor. So token-validated sources only ingest when handed the
real stored token (simulating post-OAuth state). Non-token sources (mercury "any token")
work directly. This is a test-harness integration gap, not a Fyralis client defect.

**Headline:** the spammer is faithful to real APIs; it cleanly separates Fyralis's REAL
ingestion clients (slack, github, discord, gmail/calendar/drive, notion, jira, quickbooks,
grafana, ashby, miro, mercury, aws) from the **finance/entity PLACEHOLDER clients**
(brex, deel, ramp, gusto, figma, fireflies, carta, linkedin, hibob) that only work against
Fyralis's own (wrong) embedded mocks and 404 against real-API behavior.

## B. MODEL-LAYER infrastructure bugs FOUND + FIXED (so the model layer can run)
1. **pgvector codec crash (FIXED)** — `services/reasoning/retrieval/pathways.py:1510`
   stringified the query vector when `_conn_has_vector_codec` returned False, but if the
   codec was actually live on the connection the `'[…]'::vector` text bind crashes with
   "could not convert string to float" (run_100 / think both hit this → 0 models). Fixed by
   always `_ensure_vector_codec(conn)` + binding a numpy array.
2. **global pool lacked codec (FIXED in harness)** — `lib/shared/db.py:126` creates the
   global pool WITHOUT `init=_register_codecs`; think sub-components using `get_pool()`
   read `vector` columns back as strings. run_100 now points the global slot at the
   codec-registered pool.
3. **LLM provider blocked (EXTERNAL)** — DeepSeek returns **HTTP 402 Payment Required**
   (account out of balance); ANTHROPIC/OPENAI/NEXUS keys are empty in .env. Worked around
   with local **Ollama qwen2.5:7b-instruct** via a new `LLM_BASE_URL` env override on the
   OpenAI-compatible provider (lib/llm/provider.py). NOTE: a capable cloud LLM (top up
   DeepSeek or add a key) is needed for production-quality model-layer reasoning.

## C. MODEL LAYER — status

Three blockers found + fixed to get the model layer running on this box:
1. **pgvector codec crash** (FIXED, real bug) — `pathways.py` stringified the retrieval
   query vector when `_conn_has_vector_codec` returned False while the codec was actually
   live → "could not convert string to float" → every model write crashed (0 models).
   Fixed: always `_ensure_vector_codec(conn)` + bind numpy. Confirmed: 0 codec errors after.
2. **global pool lacked codec** (FIXED in harness) — `lib/shared/db.py` global pool has no
   `init=_register_codecs`; pointed run_100's global slot at the codec-registered pool.
3. **LLM unavailable** (EXTERNAL + worked around) — DeepSeek HTTP 402 (out of balance);
   ANTHROPIC/OPENAI keys empty. Wired local **Ollama qwen2.5:7b-instruct** via a new
   `LLM_BASE_URL` env override (lib/llm/provider.py). Then hit **`LLM_TIMEOUT_SECONDS=30`
   default** → every think call "Request timed out" on slow CPU inference (0 models).
   Fixed by raising `LLM_TIMEOUT_SECONDS` (900) for local inference.

4. **LLM capability** (the remaining gate) — with infra + timeouts fixed (0 codec errors,
   0 timeouts), think over Alpen mercury observations with qwen2.5:7b STILL produced 0
   models: `error_type=ReasoningFailure` — "LLM output did not validate ... RawDiffClaimsOnly".
   qwen2.5:7b emits **schema-invalid diffs**: placeholder UUIDs (`TENANT_123`, `P1`,
   `external_signal_integration`) instead of real ids, and the wrong structure (a bare
   `proposition` instead of `claim_ops:[{op:'insert',…}]`). It produces *sensible content*
   but cannot follow Fyralis's strict structured-output contract. Fyralis defaults to
   `deepseek-reasoner` precisely because the model layer needs a capable model.

5. **Capable model (qwen2.5:14b) confirms the pipeline is sound** — re-ran the Alpen
   model layer with 14b: **`ReasoningFailure: 0`** (the schema-invalid problem is GONE —
   14b follows Fyralis's `RawDiffClaimsOnly` diff contract), retrieval clean, no codec
   errors. BUT **2 `think.worker.run_timeout`** — each think run took **>1 hour on CPU**
   (14b is ~impractically slow for Fyralis's multi-call reasoning on CPU-only Ollama).

**Net / model-layer verdict:** The model-layer pipeline is **infrastructurally sound** — I
found+fixed a real Fyralis bug (pgvector codec) that was silently producing 0 models, plus
the global-pool codec + LLM timeout. The pipeline ingests Alpen observations → enqueues T1
triggers → retrieves over embeddings → calls the LLM **without error**, and a capable model
(14b) produces **schema-valid** reasoning. The two local options bracket the requirement:
  - qwen2.5:**7b** = fast enough on CPU, but **too weak** → schema-invalid diffs → 0 models.
  - qwen2.5:**14b** = **capable** (schema-valid) but **too slow** on CPU (>1h/run → timeout).
So producing the actual Alpen models needs a model that is BOTH capable AND fast = a
**cloud LLM**. The configured **DeepSeek is out of balance (HTTP 402)**; ANTHROPIC/OPENAI
keys are empty. **To finish model-layer testing on Alpen data: restore a capable LLM** —
top up DeepSeek (key is valid, just $0) or set `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` in
`fyraliscore/.env`. With that, the model layer will run to completion (the infra is fixed).

### LLM options tried (all 4 externally blocked)
- **DeepSeek** (configured default) — HTTP **402 Payment Required** (account $0 balance).
- **Codex** (`~/.codex/auth.json`, ChatGPT OAuth) — **EXPIRED**: access token expired ~6
  days ago, refresh token already consumed (single-use) → `codex` CLI gets 401
  ("refresh token was already used. Please log out and sign in again"). Needs `codex login`.
- **Anthropic / OpenAI** — keys empty in `.env`.
- **Local Ollama** — qwen2.5:7b (fast, schema-INVALID) / 14b (schema-VALID, too slow on CPU).

**→ One working capable LLM unblocks everything (infra is fixed). Fastest: `codex login`
to refresh `~/.codex/auth.json` (free via ChatGPT sub), then `LLM_PROVIDER=codex
CODEX_TRANSPORT=app-server LLM_MODEL=gpt-5.5`. Or top up DeepSeek, or add an API key.**

### ✅ UNBLOCKED — model layer WORKING with codex/gpt-5.5
After `codex login` refreshed the auth, `LLM_PROVIDER=codex CODEX_TRANSPORT=app-server
LLM_MODEL=gpt-5.5` works end-to-end: **0 ReasoningFailure, 0 codec errors, 0 timeouts**.
The model layer over Alpen **mercury** data produced **9 schema-valid models** in one run:
  - `belief` "Alpen Labs Treasury has $2,500,000.00 available"
  - `belief` "$10,000.00 externalTransfer outflow to Vendor was sent (2025-12-26)"
  - `belief` "Alpen Labs Checking balance was $14,734,255.00 available"
  - + composite-situation aggregations.
Quality notes: grounded + correct vs the mercury ground truth, but **redundant** (several
near-duplicate beliefs about the same balance/transaction — consolidation/dedup is weak on
a short run).

**Model-layer robustness bug found — `modality` CheckViolation:** in the slack run, 10/14
think runs failed with `asyncpg CheckViolationError: new row for relation "models" violates
check constraint "models_modality_valid"`. The DB enforces `modality ∈
{observed,inferred,expected,normative}`, but the LLM diff sometimes yields a value outside
that set and the applier **doesn't coerce/validate `modality` before insert** → the whole
think run aborts and the model is lost. (Schema validation `RawDiffClaimsOnly` passes, so
it's a gap between the Pydantic diff schema and the DB CHECK.) Same class as the codec bug —
an LLM-output value that's accepted upstream but rejected at the DB. Worth fixing (coerce or
validate `modality` in the applier) so capable LLMs don't lose ~half their models.

### Slack → model layer (the rich "Alpen insights" run): 20 obs → 45 models
codex/gpt-5.5 over 20 real Alpen Slack messages produced **45 schema-valid models**
(37 belief, 4 norm, 3 prediction, 1 observation; claim_roles: 20 fact, 12 situation,
6 concern, 4 recommendation, 3 prediction). Assessment vs ground truth:
- **Correct + grounded (22/45)** reference real Alpen systems: "the **bridge** fix was
  merged", "wired through **strata-common**", "add the malformed **proof** case",
  "serialization mismatch found", "ship after the **fixtures** pass", norms like
  "document invariants next to the code". Accurate reflections of the source messages,
  connected to Alpen's actual products (Strata, Strata Bridge, Glock proofs).
- **Under-scoped (22/45)**: models say "the *external Slack sender*" and "the affected
  commitment/goal is *not identified in the provided context*" — **the KEY insight**: a
  single-source isolated run has no org graph, so the model layer can't attribute
  messages to specific Alpen people or link them to specific initiatives. In a full
  multi-source Fyralis run (people from HR/slack/github, projects from jira/github, etc.)
  these would resolve to named engineers + Strata/Glock/bridge commitments.
- **Redundancy**: ~1/3 are near-duplicate "Composite situation" beliefs of an atomic one.
- **Modality bug** cost 11 of 30 think runs (lost models) — see above.

**Model-layer verdict:** WORKS and is broadly CORRECT. With a capable LLM (codex/gpt-5.5)
it reliably extracts grounded beliefs/norms/predictions from Alpen data. Its main quality
limits are (1) the `modality` CheckViolation bug, (2) weak dedup/consolidation, and (3) it
is only as rich as the cross-source org graph — single-source runs leave actors and
commitments unscoped. The infra is sound; richer insight needs a full multi-source ingest
(which in turn needs the placeholder ingestion clients in §A fixed, plus real OAuth tokens).

### Status vs latest `main` (pulled `bc729693`, 2026-06-11) + fixes committed
Re-checked each bug we found against the freshly-pulled `main`:
- **`modality` CheckViolation — ALREADY FIXED in main** (`e85db9da` / `1dc1f311`:
  `sanitize_explicit_grammar_axes` + `_coerce_update_value`). The Fyralis team hit the same
  bug independently (`modality="actual"` ×33 in their 2026-06-11 heavy-spam e2e). ✅ no action.
- **pgvector codec crash — STILL PRESENT in main** (`pathways.py:1510/1516` unchanged). This
  is the bug that produced **0 models**. → FIXED by us.
- **Mercury 30-day backfill truncation — STILL PRESENT in main.** → FIXED by us
  (verified: cold backfill now ingests **881 = 878 txns + 3 snapshots = full ground truth**,
  up from 58).
- **Placeholder ingestion clients — STILL PRESENT** (brex/deel/ramp/gusto/carta/linkedin/
  figma/fireflies/hibob). Documented in `FYRALIS_PLACEHOLDER_INGESTION_CLIENTS.md`; these are
  full rewrites, not committed here.

Committed on branch **`fix/model-layer-pgvector-codec-and-mercury-backfill`**:
1. `services/reasoning/retrieval/pathways.py` — ensure pgvector codec + bind numpy in
   retrieval pathway B (was crashing every model write → 0 models).
2. `services/ingest/ingestion/fetchers/mercury.py` — pin a cold-backfill `start=` floor so
   Mercury doesn't truncate history to 30 days (+ `MERCURY_BACKFILL_FLOOR_DAYS` knob).

### Other Fyralis-core changes (not in the fix branch; test/feature)
- `services/reasoning/retrieval/pathways.py` — **bug fix**: ensure pgvector codec + bind
  numpy (was crashing all model writes with "could not convert string to float").
- `scripts/run_100_signal_real_llm_e2e.py` — point the global pool slot at the codec pool.
- `lib/llm/provider.py` — add `LLM_BASE_URL` env override (point OpenAI-compatible provider
  at a local/alternate endpoint, e.g. Ollama).
Test harnesses (mine, in fyraliscore/): `_proof_mercury_spammer.py`, `_sweep_finance.py`,
`_connsweep.py`.
