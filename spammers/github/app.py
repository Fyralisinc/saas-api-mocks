"""GitHub mock — FastAPI app factory.

Usage:
    python -m spammers.github run --port 7003
"""
from __future__ import annotations

import hashlib
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.responses import Response

from spammers.github import state as _state
from spammers.github.responses import GitHubJSONResponse
from spammers.github.routes import (
    app_api as _app_api,
    install as _install,
    installation as _installation,
    repo_content as _repo_content,
    repos as _repos,
)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    await _state.startup()
    yield
    await _state.shutdown()


_DEFAULT_API_VERSION = "2022-11-28"


def _etag_matches(if_none_match: str, etag: str) -> bool:
    if if_none_match.strip() == "*":
        return True
    candidates = {t.strip().removeprefix("W/") for t in if_none_match.split(",")}
    return etag in candidates or f'W/{etag}' in candidates


async def _github_headers(request: Request, call_next):
    """Attach GitHub's standard response headers; add ETag + 304 handling.

    Real GitHub returns ETag on every response and honors If-None-Match with a
    304 (which does not count against the rate limit).
    """
    response = await call_next(request)
    response.headers["Server"] = "GitHub.com"
    response.headers["X-GitHub-Media-Type"] = "github.v3; format=json"
    response.headers["X-GitHub-Api-Version"] = request.headers.get(
        "X-GitHub-Api-Version", _DEFAULT_API_VERSION
    )
    response.headers["X-GitHub-Request-Id"] = secrets.token_hex(8).upper()

    # ETag + conditional requests for JSON GET 200s.
    if request.method == "GET" and response.status_code == 200 and hasattr(response, "body_iterator"):
        ctype = response.headers.get("content-type", "application/json; charset=utf-8")
        body = b"".join([chunk async for chunk in response.body_iterator])
        etag = '"' + hashlib.md5(body).hexdigest() + '"'  # noqa: S324 (not security-sensitive)
        headers = {k: v for k, v in response.headers.items()
                   if k.lower() not in ("content-length", "content-type")}
        inm = request.headers.get("if-none-match")
        if inm and _etag_matches(inm, etag):
            inst = getattr(request.state, "gh_rl_installation", None)
            if inst is not None:
                from spammers.github.ratelimit import peek_headers, refund
                refund(inst)                        # a 304 must not count against the limit
                # Replace the (already-consumed) rate-limit headers with the
                # refunded values; drop the stale lowercase copies first.
                headers = {k: v for k, v in headers.items() if not k.lower().startswith("x-ratelimit-")}
                headers.update(peek_headers(inst))
            headers["ETag"] = etag
            return Response(status_code=304, headers=headers)
        headers["ETag"] = etag
        return Response(content=body, status_code=200, headers=headers, media_type=ctype)
    return response


def create_app() -> FastAPI:
    app = FastAPI(title="GitHub mock", lifespan=_lifespan, default_response_class=GitHubJSONResponse)
    app.middleware("http")(_github_headers)
    app.include_router(_install.router)
    app.include_router(_app_api.router)
    app.include_router(_installation.router)
    app.include_router(_repo_content.router)
    app.include_router(_repos.router)

    @app.get("/_health")
    async def health():
        return {"ok": True, "service": "github-mock"}

    @app.post("/_control/secondary_limit")
    async def arm_secondary(request: Request):
        """Mock-only control (not a GitHub route): arm the next ``count`` requests
        for an installation to hit the secondary rate limit (429 + Retry-After)."""
        from spammers.github.ratelimit import arm_secondary_limit
        qp = request.query_params
        try:
            installation_id = int(qp["installation_id"])
        except (KeyError, ValueError):
            return GitHubJSONResponse({"message": "installation_id (int) required"}, status_code=400)
        count = int(qp.get("count", 1))
        retry_after = int(qp.get("retry_after", 60))
        arm_secondary_limit(installation_id, count=count, retry_after=retry_after)
        return {"armed": True, "installation_id": installation_id,
                "count": count, "retry_after": retry_after}

    return app


app = create_app()
