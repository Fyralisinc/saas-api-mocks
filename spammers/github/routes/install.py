"""GET /apps/{slug}/installations/new — the App install page.

Real GitHub shows an account picker and an "Install" button, then redirects to
the App's setup URL with ``installation_id`` + ``setup_action=install`` + ``state``.
The mock auto-approves: it resolves the App's seeded installation and redirects
to ``redirect_uri`` (the consumer's callback).
"""
from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from spammers.github.state import state as get_state

router = APIRouter()


@router.get("/apps/{slug}/installations/new")
async def install_new(request: Request, slug: str, state: str = "", redirect_uri: str = ""):  # noqa: A002
    st = get_state()
    row = await st.pool.fetchrow(
        """
        SELECT inst.installation_id
          FROM app_github.apps a
          JOIN app_github.installations inst ON inst.app_pk = a.id
         WHERE a.run_id = $1 AND a.slug = $2
         ORDER BY inst.installation_id
         LIMIT 1
        """,
        st.run_id, slug,
    )
    if row is None:
        return HTMLResponse(
            "<!doctype html><html><body><h1>Not Found</h1>"
            "<p>No such GitHub App.</p></body></html>",
            status_code=404,
        )
    params = {"installation_id": row["installation_id"], "setup_action": "install"}
    if state:
        params["state"] = state
    target = f"{redirect_uri}?{urlencode(params)}" if redirect_uri else f"?{urlencode(params)}"
    return HTMLResponse(
        f"""<!doctype html>
<html><head><title>Install (mock)</title>
<meta http-equiv="refresh" content="0; url={target}">
</head><body>
<h1>GitHub mock — auto-approving install</h1>
<p>Redirecting to <a href="{target}">{target}</a>.</p>
</body></html>
"""
    )
