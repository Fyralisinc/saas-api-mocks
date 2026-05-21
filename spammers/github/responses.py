"""GitHub returns ``Content-Type: application/json; charset=utf-8``."""
from __future__ import annotations

from fastapi.responses import JSONResponse


class GitHubJSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"
