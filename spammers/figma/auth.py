"""Auth for the Figma mock — ``X-Figma-Token`` OR ``Authorization: Bearer``.

Figma's REST read endpoints accept **either** scheme (the OpenAPI spec lists
``PersonalAccessToken``/``PlanAccessToken`` — an ``X-Figma-Token`` apiKey header —
AND ``OAuth2`` Bearer as alternative security on every read op):

    X-Figma-Token: <personal-or-plan-access-token>
    Authorization: Bearer <oauth-access-token>

The mock is single-tenant per run: it does not match a provisioned token, it
accepts any NON-EMPTY token presented in either header, and treats a missing/blank
one as unauthenticated. The caller turns that into Figma's documented file-scoped
auth failure — **403** (NOT 401) with the ``{status, err}`` err-message envelope
(developers.figma.com errors / the per-endpoint 403 declarations for files,
versions and comments).
"""
from __future__ import annotations

from typing import Optional


def access_token(request) -> Optional[str]:
    """Return the presented token from ``X-Figma-Token`` or ``Authorization: Bearer``.

    ``X-Figma-Token`` takes precedence (the PAT path). Returns None when neither a
    non-empty figma token nor a non-empty Bearer is present.
    """
    xf = request.headers.get("x-figma-token") or request.headers.get("X-Figma-Token")
    if xf and xf.strip():
        return xf.strip()
    h = request.headers.get("authorization") or request.headers.get("Authorization")
    if h:
        h = h.strip()
        if h.lower().startswith("bearer "):
            tok = h[7:].strip()
            if tok:
                return tok
    return None


def is_authed(request) -> bool:
    return access_token(request) is not None
