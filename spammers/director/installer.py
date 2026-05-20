"""Auto-install each provider into Fyralis at the configured tenant.

Slack flow (current):
  1. Director calls Fyralis ``GET /integrations/slack/install`` with the
     tenant's Bearer token; Fyralis returns 302 with the state token.
  2. Director extracts the redirect URL, which points at the slack-mock's
     ``/oauth/v2/authorize``. The mock returns an HTML page; Director
     bypasses that and pulls the ``code`` directly via the mock's own
     pre-approve helper (it follows the meta-refresh URL).
  3. Director hits the redirect_uri Fyralis registered (i.e.
     ``/integrations/slack/callback?code=&state=``).
  4. Fyralis then POSTs ``oauth.v2.access`` to the mock and persists the
     install in ``encrypted_secrets`` + ``provider_installations``.

The Director's "Bearer token" for Fyralis tenant auth comes from
``FYRALIS_API_TOKEN`` env var (the same token a real customer's tenant
admin would use).
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlparse, parse_qs
from uuid import UUID

import httpx
import structlog


log = structlog.get_logger("spammers.installer")


_HREF_RE = re.compile(r'<a\s+href="([^"]+)">', re.IGNORECASE)
_META_REFRESH_RE = re.compile(
    r'<meta\s+http-equiv="refresh"\s+content="\d+;\s*url=([^"]+)"', re.IGNORECASE,
)


async def install_slack(
    *,
    fyralis_base: str,
    fyralis_api_token: str,
    slack_mock_base: str,
    tenant_id: UUID,
) -> dict:
    """Walk the full OAuth flow. Returns Fyralis's final install row."""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as cli:
        # Step 1: ask Fyralis for the install URL
        r = await cli.get(
            f"{fyralis_base}/integrations/slack/install",
            headers={"Authorization": f"Bearer {fyralis_api_token}"},
        )
        if r.status_code not in (302, 303, 307):
            raise RuntimeError(
                f"Fyralis /integrations/slack/install returned {r.status_code}: {r.text[:300]}"
            )
        slack_authorize_url = r.headers.get("location")
        if not slack_authorize_url:
            raise RuntimeError("Fyralis install endpoint did not return a Location header")
        # If Fyralis points at real slack.com, redirect to our mock.
        slack_authorize_url = _rebase(slack_authorize_url, slack_mock_base)
        log.info("slack_install_redirect", url=slack_authorize_url)

        # Step 2: hit the mock authorize page; extract meta-refresh callback URL
        r = await cli.get(slack_authorize_url)
        if r.status_code != 200:
            raise RuntimeError(
                f"slack-mock authorize returned {r.status_code}: {r.text[:300]}"
            )
        callback_url = _extract_callback(r.text)
        if not callback_url:
            raise RuntimeError("slack-mock did not return a redirect URL in its approve page")
        log.info("slack_install_callback", url=callback_url)

        # Step 3: drive the callback to Fyralis
        r = await cli.get(callback_url)
        if r.status_code not in (200, 302, 303, 307):
            raise RuntimeError(
                f"Fyralis callback returned {r.status_code}: {r.text[:300]}"
            )

        log.info("slack_install_done", status=r.status_code)
        return {"status": r.status_code, "callback": callback_url}


def _rebase(url: str, new_base: str) -> str:
    """Replace the scheme+host+port of ``url`` with ``new_base``."""
    parsed = urlparse(url)
    new = urlparse(new_base)
    return f"{new.scheme}://{new.netloc}{parsed.path}" + (f"?{parsed.query}" if parsed.query else "")


def _extract_callback(html: str) -> str | None:
    m = _META_REFRESH_RE.search(html)
    if m:
        return m.group(1)
    m = _HREF_RE.search(html)
    if m:
        return m.group(1)
    return None
