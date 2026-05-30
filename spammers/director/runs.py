"""Run-lifecycle helpers: create / get / list."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import UUID, uuid4

import asyncpg


async def create_run(
    pool: asyncpg.Pool,
    *,
    size: Literal["small", "medium", "large"],
    runtime: Literal["few_months", "one_year", "few_years"],
    seed: int,
    fyralis_tenant_id: UUID,
    fyralis_base_url: str,
    archetype: str = "early_saas",
    virtual_now: Optional[datetime] = None,
    profile_kind: Literal["profile", "corpus"] = "profile",
    corpus_path: Optional[str] = None,
) -> UUID:
    rid = uuid4()
    vn = virtual_now or datetime.now(timezone.utc)
    if vn.tzinfo is None:
        vn = vn.replace(tzinfo=timezone.utc)
    await pool.execute(
        """
        INSERT INTO org.runs
            (id, size, runtime, seed, archetype, fyralis_tenant_id, fyralis_base_url,
             virtual_now, mode, speed_multiplier, profile_kind, corpus_path)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'frozen', 1.0, $9, $10)
        """,
        rid, size, runtime, seed, archetype, fyralis_tenant_id, fyralis_base_url, vn,
        profile_kind, corpus_path,
    )
    return rid


async def get_run(pool: asyncpg.Pool, run_id: UUID) -> dict:
    row = await pool.fetchrow("SELECT * FROM org.runs WHERE id = $1", run_id)
    if row is None:
        raise LookupError(f"run not found: {run_id}")
    return dict(row)


async def latest_run(pool: asyncpg.Pool) -> Optional[UUID]:
    row = await pool.fetchrow("SELECT id FROM org.runs ORDER BY created_at DESC LIMIT 1")
    return row["id"] if row else None
