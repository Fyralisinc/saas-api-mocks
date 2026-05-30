# gharelu — Gharelu-Alpen corpus

A frozen 4-year corpus derived from public sources for **Gharelu-Alpen**, a
homemade high-fidelity simulation of Alpen Labs (BitVM / Strata). Consumed
by the spammer's `spammers/corpus/` replayer to drive Fyralis model-layer
testing.

## Layers

- `facts/` — L1: hand-curated YAML (people, products, milestones, voices, patterns, office_life, chatter)
- `raw/` — scraped real data (blog HTML, docs; `raw/github/` is gitignored, regenerable via `make scrape-github`)
- `threads/` — L3: per-initiative story arcs (LLM-generated, cached)
- `artifacts/` — high-signal LLM prose (RFCs, postmortems, design docs)
- `build/events.jsonl` — L4: final replay log consumed by `spammers/corpus/replay.py`

## Build

```
make corpus        # full pipeline: scrape → facts → timeline → threads → artifacts → voices → patterns → office-life → chatter → render
make render        # just re-render events.jsonl from existing inputs
```

The L4 artifact `build/events.jsonl` is committed (~10 MB). Anything else
in `build/` is gitignored — re-derivable from facts + threads + artifacts.

## Replay

From the spammer repo root:

```
./dev.sh prepare        # backfills build/events.jsonl into the mock DBs as-of 2025-11-28
```

Override the corpus path or cutoff via `CORPUS_PATH=` and `AS_OF=` env vars.
