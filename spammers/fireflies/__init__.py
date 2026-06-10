"""Fireflies.ai (AI meeting-notetaker) mock — the REAL GraphQL API.

Fireflies' real surface is a single ``POST https://api.fireflies.ai/graphql``
exposing ``transcripts``/``transcript``/``user`` queries — NOT the fake Brex REST
paths the Fyralis flow doc clones. See ``app.py`` + the fireflies-fidelity-audit
memory for the full contract + the logged Fyralis-vs-real divergences.
"""
