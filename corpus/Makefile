.PHONY: setup scrape scrape-github scrape-blog scrape-docs \
        facts enrich timeline threads artifacts voices patterns \
        office-life chatter render corpus clean-build clean-cache

PY := .venv/bin/python
LOAD_ENV := set -a; . ./.env; set +a;

setup:
	python3 -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -q httpx pyyaml anthropic

scrape: scrape-github scrape-blog scrape-docs

scrape-github:
	$(LOAD_ENV) $(PY) scripts/01_scrape.py github

scrape-blog:
	$(LOAD_ENV) $(PY) scripts/01_scrape.py blog

scrape-docs:
	$(LOAD_ENV) $(PY) scripts/01_scrape.py docs

# L1: build draft facts.yaml then enrich people via GitHub profiles + heuristics.
# Drops 3 external contributors, leaves ~10 full_names as 'needs:review' for spot-check.
facts:
	$(LOAD_ENV) $(PY) scripts/build_facts.py
	cp -n facts/facts.yaml.draft facts/facts.yaml 2>/dev/null || true
	$(LOAD_ENV) $(PY) scripts/enrich_people.py

# L1 → L2: deterministic milestone expansion.
timeline:
	$(LOAD_ENV) $(PY) scripts/02_plan_timeline.py

# L3: thread specs via LLM (one per anchor: milestone + ongoing repo work).
threads:
	$(LOAD_ENV) LLM_PROVIDER=$${LLM_PROVIDER:-codex} $(PY) scripts/03_plan_threads.py --budget-usd 999

# L4: high-signal RFCs / postmortems / design docs via LLM.
artifacts:
	$(LOAD_ENV) LLM_PROVIDER=$${LLM_PROVIDER:-codex} $(PY) scripts/04_generate_artifacts.py --budget-usd 999

# Voice profile + work snippet pool per person.
voices:
	$(LOAD_ENV) LLM_PROVIDER=$${LLM_PROVIDER:-codex} $(PY) scripts/06_generate_voices.py --budget-usd 999

# Deterministic behavioral patterns (ship lag, hours, weekend, standup).
patterns:
	$(LOAD_ENV) $(PY) scripts/07_compile_patterns.py

# PTO calendar + external events + conference travelers.
office-life:
	$(LOAD_ENV) $(PY) scripts/08_compile_office_life.py

# Per-person off-topic chatter (reactions, news, PTO announce, conference posts).
chatter:
	$(LOAD_ENV) LLM_PROVIDER=$${LLM_PROVIDER:-codex} $(PY) scripts/09_generate_chatter.py --budget-usd 999

# L5: render events.jsonl from everything above. --include-all turns on every
# category: github mirror, rituals, chatter, 1:1s, ci flakes, hiring, gmail
# external comms, notion edit history.
render:
	$(LOAD_ENV) $(PY) scripts/05_render_events.py --include-all

# Full pipeline.
corpus: scrape facts timeline threads artifacts voices patterns office-life chatter render

clean-build:
	rm -rf build/

clean-cache:
	rm -rf cache/
