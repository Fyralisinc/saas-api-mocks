"""Discord returns a bare ``Content-Type: application/json`` (no charset).

discord.py's ``json_or_text`` does an *exact* string match on
``application/json``; a ``; charset=utf-8`` suffix makes the stock client
treat every response body as plain text and fail to build its models. Real
Discord omits the charset, so we do too.
"""
from __future__ import annotations

from fastapi.responses import JSONResponse


class DiscordJSONResponse(JSONResponse):
    media_type = "application/json"
