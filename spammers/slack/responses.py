"""Slack returns ``Content-Type: application/json; charset=utf-8``.

Starlette's default ``JSONResponse`` emits ``application/json`` with no charset,
so we pin the media type here and use this everywhere the mock returns JSON.
"""
from __future__ import annotations

from fastapi.responses import JSONResponse


class SlackJSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"
