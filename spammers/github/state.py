"""Per-process state for the GitHub mock (mirrors spammers/slack/state.py)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

import asyncpg

from spammers.common.db import create_pool
from spammers.common.rate_limit import RateLimiter


@dataclass
class GitHubMockState:
    pool: asyncpg.Pool
    run_id: UUID
    rate_limiter: RateLimiter
    # Fixed hourly rate-limit windows, keyed by installation id:
    # {installation_id: {"reset": epoch_seconds, "used": int}}
    windows: dict = field(default_factory=dict)
    # Armed secondary (abuse) rate limits, keyed by installation id:
    # {installation_id: {"remaining": int, "retry_after": int}} — each consumes
    # one armed unit and returns a 429 + Retry-After, like GitHub's abuse limit.
    secondary: dict = field(default_factory=dict)


_STATE: Optional[GitHubMockState] = None


async def startup(run_id: UUID | None = None) -> GitHubMockState:
    global _STATE
    if _STATE is not None:
        return _STATE
    pool = await create_pool()
    rid = run_id
    if rid is None:
        rid_env = os.environ.get("SPAMMER_RUN_ID")
        if rid_env:
            rid = UUID(rid_env)
        else:
            row = await pool.fetchrow("SELECT id FROM org.runs ORDER BY created_at DESC LIMIT 1")
            if row is None:
                raise RuntimeError("no run found in org.runs; create one via Director first")
            rid = row["id"]
    _STATE = GitHubMockState(pool=pool, run_id=rid, rate_limiter=RateLimiter())
    return _STATE


async def shutdown() -> None:
    global _STATE
    if _STATE is not None:
        await _STATE.pool.close()
        _STATE = None


def state() -> GitHubMockState:
    if _STATE is None:
        raise RuntimeError("github mock state not initialised — call startup() first")
    return _STATE
