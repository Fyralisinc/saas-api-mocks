"""Inbound GitHub App-JWT validation.

The App API (``/app``, ``/app/installations…``) is authenticated with a
short-lived JWT the consumer signs with the App's RSA private key (RS256),
``iss`` = the numeric app id. The mock verifies it against the App's stored
public key.
"""
from __future__ import annotations

from typing import Optional

import jwt
from fastapi import Request

from spammers.github.auth import bearer
from spammers.github.state import state


async def resolve_app(request: Request) -> Optional[dict]:
    """Return the App row if the request carries a valid App JWT, else None."""
    token = bearer(request)
    if not token:
        return None
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
    except Exception:
        return None
    iss = str(unverified.get("iss", ""))
    if not iss:
        return None
    st = state()
    app = await st.pool.fetchrow(
        "SELECT * FROM app_github.apps WHERE run_id = $1 AND app_id::text = $2",
        st.run_id, iss,
    )
    if app is None:
        return None
    try:
        jwt.decode(
            token,
            app["public_key"],
            algorithms=["RS256"],
            issuer=iss,
            options={"verify_aud": False},
        )
    except Exception:
        return None
    return dict(app)
