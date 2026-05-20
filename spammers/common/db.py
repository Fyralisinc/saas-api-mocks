"""Async Postgres pool for the mock-orgs DB.

This DB is SEPARATE from Fyralis's database. The URL is read from
SPAMMERS_DB_URL (default: postgresql://postgres:postgres@localhost:5432/mock_orgs).

Migrations live in spammers/db/migrations/*.sql. They are applied with
``await apply_migrations(pool)`` at Director startup.
"""
from __future__ import annotations

import os
import pathlib
from typing import Optional

import asyncpg


_DEFAULT_URL = "postgresql://postgres:postgres@localhost:5432/mock_orgs"
_MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "db" / "migrations"


def db_url() -> str:
    return os.environ.get("SPAMMERS_DB_URL", _DEFAULT_URL)


async def create_pool(min_size: int = 1, max_size: int = 10) -> asyncpg.Pool:
    """Open an asyncpg pool against the mock-orgs DB."""
    return await asyncpg.create_pool(
        dsn=db_url(),
        min_size=min_size,
        max_size=max_size,
    )


async def apply_migrations(pool: asyncpg.Pool) -> list[str]:
    """Apply every .sql file in spammers/db/migrations/ in filename order.

    Each migration is wrapped in a transaction. ``CREATE … IF NOT EXISTS``
    semantics make every migration idempotent.
    """
    applied: list[str] = []
    files = sorted(p for p in _MIGRATIONS_DIR.glob("*.sql"))
    async with pool.acquire() as conn:
        for path in files:
            sql = path.read_text(encoding="utf-8")
            async with conn.transaction():
                await conn.execute(sql)
            applied.append(path.name)
    return applied


async def ensure_database_exists() -> None:
    """Connect to the postgres maintenance DB and create mock_orgs if absent.

    Idempotent. Honors SPAMMERS_DB_URL's host/port/user/pass; only the
    database name segment is swapped to 'postgres' for the bootstrap connect.
    """
    url = db_url()
    # asyncpg accepts both postgres:// and postgresql:// URLs.
    # We need a maintenance connection — connect to "postgres" DB.
    # Simplest reliable parse: rsplit on '/' once.
    base, target_db = url.rsplit("/", 1)
    target_db = target_db.split("?", 1)[0]
    if not target_db:
        return  # caller specified no DB name; nothing to do
    maint_url = f"{base}/postgres"
    conn: Optional[asyncpg.Connection] = None
    try:
        conn = await asyncpg.connect(maint_url)
        row = await conn.fetchrow(
            "SELECT 1 FROM pg_database WHERE datname = $1",
            target_db,
        )
        if row is None:
            # CREATE DATABASE cannot run inside a transaction.
            await conn.execute(f'CREATE DATABASE "{target_db}"')
    finally:
        if conn is not None:
            await conn.close()
