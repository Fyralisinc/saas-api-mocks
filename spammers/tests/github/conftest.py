"""Fixtures for the GitHub-mock fidelity suite.

Reuses the session ``pool`` from the top-level conftest, seeds a deterministic
GitHub App (with a known RSA keypair so tests can sign App JWTs), one
installation, and three repositories, then wires the GitHub ``state`` singleton
to that run and exposes an ASGI client.
"""
from __future__ import annotations

import json
import time
from uuid import UUID, uuid4

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from spammers.common.signing import generate_rsa_keypair

# Deterministic identifiers the tests assert against.
APP_ID = 424242
SLUG = "acme-ingest"
APP_NAME = "Acme Ingest"
CLIENT_ID = "Iv1.deadbeefdeadbeef"
INSTALLATION_ID = 55555555
ACCOUNT_LOGIN = "acme"
ACCOUNT_ID = 9001
REPOS = [("acme", "core", 101), ("acme", "billing", 102), ("acme", "web", 103)]

# Known content seeded on acme/core (PR and issue numbers share one sequence).
SHA1 = "a1" * 20
PR_OPEN = 1
PR_MERGED = 2
ISSUE_NUM = 3

_PRIVATE_PEM, _PUBLIC_PEM = generate_rsa_keypair()


def app_jwt(app_id: int = APP_ID, private_pem: str = _PRIVATE_PEM) -> str:
    now = int(time.time())
    return jwt.encode(
        {"iat": now - 60, "exp": now + 540, "iss": str(app_id)},
        private_pem, algorithm="RS256",
    )


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def gh_run(pool) -> UUID:
    run_id = uuid4()
    await pool.execute(
        """
        INSERT INTO org.runs (id, size, runtime, seed, fyralis_tenant_id,
                              fyralis_base_url, virtual_now, mode, speed_multiplier)
        VALUES ($1, 'small', 'few_months', 2, $2, 'http://localhost:8000', now(), 'frozen', 1.0)
        """,
        run_id, uuid4(),
    )
    await pool.execute(
        """
        INSERT INTO org.people (id, run_id, handle, full_name, email, role, level, timezone, started_at)
        VALUES ($1, $2, 'octocat', 'Octo Cat', 'octo@acme.test', 'engineer', 'senior', 'UTC', now())
        """,
        uuid4(), run_id,
    )
    app_pk = uuid4()
    await pool.execute(
        """
        INSERT INTO app_github.apps
            (id, run_id, app_id, slug, name, client_id, client_secret, webhook_secret,
             private_key, public_key, permissions, events)
        VALUES ($1, $2, $3, $4, $5, $6, 'secret', 'whsec', $7, $8, $9::jsonb, $10::jsonb)
        """,
        app_pk, run_id, APP_ID, SLUG, APP_NAME, CLIENT_ID, _PRIVATE_PEM, _PUBLIC_PEM,
        json.dumps({"contents": "read", "metadata": "read"}),
        json.dumps(["push", "pull_request"]),
    )
    inst_pk = uuid4()
    await pool.execute(
        """
        INSERT INTO app_github.installations
            (id, app_pk, installation_id, account_login, account_type, account_id,
             repository_selection, created_at)
        VALUES ($1, $2, $3, $4, 'Organization', $5, 'all', now())
        """,
        inst_pk, app_pk, INSTALLATION_ID, ACCOUNT_LOGIN, ACCOUNT_ID,
    )
    repo_pks: dict[str, UUID] = {}
    for owner, name, repo_id in REPOS:
        repo_pk = uuid4()
        repo_pks[name] = repo_pk
        await pool.execute(
            """
            INSERT INTO app_github.repositories
                (id, installation_pk, repo_id, owner, name, private, default_branch,
                 description, created_at)
            VALUES ($1, $2, $3, $4, $5, FALSE, 'main', $6, now())
            """,
            repo_pk, inst_pk, repo_id, owner, name, f"The {name} service.",
        )

    # Known content on acme/core for deterministic contract assertions.
    core = repo_pks["core"]
    await pool.execute(
        """
        INSERT INTO app_github.commits
            (id, repo_pk, sha, message, author_login, author_email, committed_at, additions, deletions)
        VALUES ($1, $2, $3, 'fix: guard nil', 'alice', 'alice@acme.test', now(), 10, 2)
        """,
        uuid4(), core, SHA1,
    )
    pr_merged_pk = uuid4()
    await pool.execute(
        """
        INSERT INTO app_github.pull_requests
            (id, repo_pk, number, title, body, state, merged, user_login, head_ref, head_sha,
             base_sha, additions, deletions, changed_files, created_at, updated_at, merged_at, closed_at)
        VALUES ($1, $2, $3, 'Add retry budget', 'body', 'closed', TRUE, 'bob', 'feature/retry',
                $4, $5, 40, 5, 3, now(), now(), now(), now())
        """,
        pr_merged_pk, core, PR_MERGED, SHA1, SHA1,
    )
    await pool.execute(
        """
        INSERT INTO app_github.pull_requests
            (id, repo_pk, number, title, body, state, merged, user_login, head_ref, head_sha,
             base_sha, created_at, updated_at)
        VALUES ($1, $2, $3, 'WIP cache layer', 'body', 'open', FALSE, 'alice', 'feature/cache',
                $4, $5, now(), now())
        """,
        uuid4(), core, PR_OPEN, SHA1, SHA1,
    )
    await pool.execute(
        """
        INSERT INTO app_github.reviews (id, pr_pk, user_login, state, body, submitted_at)
        VALUES ($1, $2, 'alice', 'approved', 'LGTM', now())
        """,
        uuid4(), pr_merged_pk,
    )
    await pool.execute(
        """
        INSERT INTO app_github.issues
            (id, repo_pk, number, title, body, state, user_login, created_at, updated_at)
        VALUES ($1, $2, $3, 'Timeouts under load', 'body', 'open', 'carol', now(), now())
        """,
        uuid4(), core, ISSUE_NUM,
    )
    await pool.execute(
        """
        INSERT INTO app_github.issue_comments (id, repo_pk, issue_number, user_login, body, created_at)
        VALUES ($1, $2, $3, 'bob', 'Looking into it.', now())
        """,
        uuid4(), core, ISSUE_NUM,
    )
    await pool.execute(
        """
        INSERT INTO app_github.check_runs
            (id, repo_pk, name, head_sha, status, conclusion, started_at, completed_at)
        VALUES ($1, $2, 'build', $3, 'completed', 'success', now(), now())
        """,
        uuid4(), core, SHA1,
    )
    return run_id


@pytest_asyncio.fixture(loop_scope="session")
async def gh_client(pool, gh_run):
    from spammers.common.rate_limit import RateLimiter
    from spammers.github import state as gh_state
    from spammers.github.app import create_app

    gh_state._STATE = gh_state.GitHubMockState(
        pool=pool, run_id=gh_run, rate_limiter=RateLimiter()
    )
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://mock") as c:
        yield c
    gh_state._STATE = None


@pytest.fixture
def jwt_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {app_jwt()}"}


@pytest_asyncio.fixture(loop_scope="session")
async def install_token(gh_client, jwt_header) -> str:
    """Mint a real installation token through the access_tokens endpoint."""
    r = await gh_client.post(
        f"/app/installations/{INSTALLATION_ID}/access_tokens", headers=jwt_header
    )
    return r.json()["token"]
