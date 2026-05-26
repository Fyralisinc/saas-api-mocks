"""Notion mock (:7006), API version 2022-06-28.

Wire-compatible with the surface the consumer's backfill tree-walk + webhook
hydration use: ``POST /v1/search``, ``POST /v1/databases/{id}/query``,
``GET /v1/blocks/{id}/children``, ``GET /v1/comments``, ``GET /v1/pages/{id}``,
``GET /v1/users/me``, and the OAuth token exchange. Cursor pagination uses
opaque ``start_cursor``/``next_cursor``/``has_more``. Serves the latest run
resolved at startup; nothing here is imported by the other mocks.
"""
