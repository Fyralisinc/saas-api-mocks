"""Google APIs return ``Content-Type: application/json; charset=UTF-8``."""
from __future__ import annotations

from fastapi.responses import JSONResponse


class GoogleJSONResponse(JSONResponse):
    media_type = "application/json; charset=UTF-8"
