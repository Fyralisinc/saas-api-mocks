"""GET /jwks — the OIDC public key the consumer verifies push tokens against.

Point the consumer's ``GOOGLE_OIDC_JWKS_URL`` at this endpoint.
"""
from __future__ import annotations

from fastapi import APIRouter

from spammers.gmail.push import jwks
from spammers.gmail.responses import GoogleJSONResponse as JSONResponse
from spammers.gmail.state import state

router = APIRouter()


@router.get("/jwks")
async def get_jwks():
    return JSONResponse(jwks(state().oidc_public_key))


# Google also serves these at /oauth2/v3/certs — accept that path too.
@router.get("/oauth2/v3/certs")
async def get_certs():
    return JSONResponse(jwks(state().oidc_public_key))
