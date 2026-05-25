"""POST /token — Google's DWD token exchange (see common.google_token)."""
from __future__ import annotations

import json
from urllib.parse import parse_qs

from fastapi import APIRouter, Request

from spammers.common.google_token import read_assertion, token_response
from spammers.gmail.responses import GoogleJSONResponse as JSONResponse

router = APIRouter()


async def _read_params(request: Request) -> dict:
    raw = await request.body()
    ctype = request.headers.get("content-type", "")
    if raw and "application/x-www-form-urlencoded" in ctype:
        return {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
    if raw and "application/json" in ctype:
        try:
            body = json.loads(raw)
            if isinstance(body, dict):
                return body
        except Exception:
            pass
    if raw:
        parsed = parse_qs(raw.decode("utf-8", "replace"))
        if parsed:
            return {k: v[0] for k, v in parsed.items()}
    return dict(request.query_params)


@router.post("/token")
async def token(request: Request):
    params = await _read_params(request)
    assertion = params.get("assertion")
    if not assertion:
        return JSONResponse(
            {"error": "invalid_request", "error_description": "Required parameter is missing: assertion"},
            status_code=400,
        )
    claims = read_assertion(assertion)
    sub = claims.get("sub") or claims.get("prn") or claims.get("iss") or "unknown"
    scope = claims.get("scope") or params.get("scope") or "https://www.googleapis.com/auth/gmail.readonly"
    if isinstance(scope, (list, tuple)):
        scope = " ".join(scope)
    return JSONResponse(token_response(sub, scope))
