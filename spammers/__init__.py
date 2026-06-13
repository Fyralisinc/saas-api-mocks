"""Spammer apps — drop-in replicas of Slack/Discord/GitHub/Gmail.

See docs/ARCHITECTURE.md for the full design. Top-level packages:

- ``spammers.common``  shared utilities (signing, rate-limit, clock, db)
- ``spammers.orggen``  deterministic organization timeline generator
- ``spammers.director``  control plane: CLI, HTTP, orchestrator
- ``spammers.slack``    Slack mock (FastAPI)
- ``spammers.discord``  Discord mock (FastAPI + Gateway WS)
- ``spammers.github``   GitHub mock (FastAPI)
- ``spammers.gmail``    Gmail mock (FastAPI)
"""
