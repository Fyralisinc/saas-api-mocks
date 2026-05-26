"""POST /token — Google's DWD token exchange.

The consumer POSTs ``grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer`` +
``assertion=<signed SA JWT>`` (form-urlencoded). We decode the assertion's
claims (without verifying — see common.google_token) to learn the impersonated
subject + scope, then mint an opaque ``ya29.…`` access token.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Request

from spammers.calendar.responses import GoogleJSONResponse as JSONResponse
from spammers.common.google_token import read_assertion, token_response

router = APIRouter()


async def _read_params(request: Request) -> dict:
    """Read params from the urlencoded form body (Google's wire format), JSON,
    or the query string — without depending on python-multipart."""
    raw = await request.body()
    ctype = request.headers.get("content-type", "")
    if raw and "application/x-www-form-urlencoded" in ctype:
        return {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
    if raw and "application/json" in ctype:
        import json
        try:
            body = json.loads(raw)
            if isinstance(body, dict):
                return body
        except Exception:
            pass
    if raw:
        # Best-effort: many clients post urlencoded without the header.
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
    scope = claims.get("scope") or params.get("scope") or "https://www.googleapis.com/auth/calendar.readonly"
    if isinstance(scope, (list, tuple)):
        scope = " ".join(scope)
    return JSONResponse(token_response(sub, scope))
