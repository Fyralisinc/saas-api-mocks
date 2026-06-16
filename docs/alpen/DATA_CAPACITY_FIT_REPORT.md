# Alpen Data Capacity Fit Report

Date: 2026-06-16

## Fyralis Caps Pinned

Latest Fyralis `main` was fetched and local `main` was fast-forwarded to
`d5f55016` (`chore(gitignore): ignore generated benchmark probe report dirs`).
The active Fyralis worktree remained on `codex/alpen-model-layer-harness`;
that branch diverges from `origin/main`, so it was not merged.

Capacity findings from latest main and local runtime:

- Embeddings: `lib/embeddings/ollama.py` still sends `content_text` to
  Ollama `/api/embeddings` with no `num_ctx` or truncation option. Local
  `nomic-embed-text` accepts 6138 repeated ASCII chars and fails at 6139 with
  `the input length exceeds the context length`; this profile uses a 5500-char
  conservative embedding budget.
- Think: `services/reasoning/think/prompt.py` keeps
  `_PER_ITEM_CHAR_LIMIT = 1500`; observations in the prompt are truncated to
  1500 chars each, and the observations section defaults to 4000 chars.
- Trigger seed text: `services/ingest/ingestion/core.py` stores
  `seed_natural_text = content_text[:2000]`.
- DB: observations use `TEXT`/`JSONB` plus `VECTOR(768)`; there is no smaller
  DB text cap.
- Kafka/raw: Fyralis still validates ingest JSON payloads at 1 MiB. Backfill
  raw records are S3 pointers, but normalized messages carry handler output.
- Source handlers still matter most:
  - Notion block `content_text` exposes only `text[:200]`.
  - Google Drive file `content_text` includes extracted file text in full.
  - Slack/Discord message handlers use message text verbatim.
  - Gmail embeds subject, snippet, and `body[:4000]`.
  - Fireflies `content_text` is title/participants truncated to 600; richer
    transcript material lives in `content`.

Latest main adds an OpenAI embedder option and new Alpen scripts
(`scripts/alpen_ingest.py`, `scripts/alpen_think_eval.py`). The default local
path remains Ollama unless env selects OpenAI. `scripts/alpen_ingest.py` writes
observations with `embedder=None` and `enqueue_trigger=False`, so the cap-fit
toggle is still best implemented in spammer data before Fyralis fetches it.

## Corpus Overflow Measurement

Full corpus: `corpus/build/events.jsonl`

- Events: 35,997
- Fields over 1500 chars: 64, all `notion.page.create.payload.body_md`
- Notion page bodies over the current block-visibility budget (180 chars):
  547 total. Of these, 64 are large enough to summarize and 483 can be split
  exactly without summarizing.
- Fields over 5500 chars: 53, all Notion page bodies
- Largest source item: 14,712 chars,
  `notion:THR-009-strata-bridge:B2`
- Current Drive/Gmail/Slack/Discord/GitHub/Jira corpus fields are below
  1500 chars.
- Current Fireflies direct seed is also below caps: transcript sentences max
  about 2372 chars in JSON, summary max about 964, while Fyralis embeds only a
  600-char transcript title/participant line.

## Toggle

The full profile (x) remains the committed source corpus:

```bash
SPAMMER_CAPACITY_PROFILE=full ./dev.sh prepare
SPAMMER_CAPACITY_PROFILE=x ./dev.sh prepare
```

The capacity-fit profile (y) is the default and derives an ignored artifact:

```bash
./dev.sh prepare
SPAMMER_CAPACITY_PROFILE=fit ./dev.sh prepare
SPAMMER_CAPACITY_PROFILE=y ./dev.sh prepare
```

Manual build:

```bash
cd corpus
make capacity-fit
```

Output:

- `corpus/build/events.capacity_fit.jsonl`
- `corpus/build/capacity_fit_report.json`
- summary cache under `corpus/cache/capacity_fit/`

## Y Profile Result

The transformer changes only Notion page body shape. It summarizes the 64
oversized Notion page bodies, and it exact-splits another 483 medium Notion
bodies into visible-size blocks. Non-oversized pages are not summarized. For
the summarized pages, it writes a short `body_md` preview plus `body_blocks`
summary paragraphs so Fyralis sees complete meaning through Notion block
`content_text` without relying on a long single item.

Verification from `corpus/build/capacity_fit_report.json`:

- Changed events: 547 / 35,997
- Changed fields: `payload.body_md` for 64 summarized pages,
  `payload.body_blocks` for 483 split-only pages
- Max y `payload.body_md`: 1401 chars
- Max y `payload.body_blocks[]`: 180 chars
- Max other tracked text field: 569 chars
- Validation errors: none

The full x corpus is not modified. When Fyralis improves its item handling, the
toggle can switch back to x immediately or the fit budgets in
`corpus/scripts/11_capacity_fit_events.py` can be raised and y regenerated.
