"""``corpus_id`` ↔ DB primary key mapping.

The corpus uses stable string identifiers (``person:alice``, ``repo:strata``).
The replayer creates DB rows on first reference and remembers the mapping so
every subsequent event resolves to the same row.

In-process cache + persistent ``org.corpus_id_map`` table. Cache is per-run
and miss-through to the DB so multi-process replay still converges.
"""
from __future__ import annotations

from uuid import UUID

import asyncpg


class IdMap:
    """Per-run, async-safe corpus_id → db_pk cache backed by org.corpus_id_map."""

    def __init__(self, pool: asyncpg.Pool, run_id: UUID) -> None:
        self.pool = pool
        self.run_id = run_id
        self._cache: dict[str, UUID] = {}

    async def warm(self) -> None:
        """Load the full mapping for this run into the cache."""
        rows = await self.pool.fetch(
            "SELECT corpus_id, db_pk FROM org.corpus_id_map WHERE run_id = $1",
            self.run_id,
        )
        self._cache = {r["corpus_id"]: r["db_pk"] for r in rows}

    async def get(self, corpus_id: str) -> UUID | None:
        if corpus_id in self._cache:
            return self._cache[corpus_id]
        pk = await self.pool.fetchval(
            "SELECT db_pk FROM org.corpus_id_map WHERE run_id = $1 AND corpus_id = $2",
            self.run_id, corpus_id,
        )
        if pk is not None:
            self._cache[corpus_id] = pk
        return pk

    async def put(self, corpus_id: str, entity_type: str, db_pk: UUID) -> None:
        await self.pool.execute(
            "INSERT INTO org.corpus_id_map (run_id, corpus_id, entity_type, db_pk) "
            "VALUES ($1, $2, $3, $4) ON CONFLICT (run_id, corpus_id) DO NOTHING",
            self.run_id, corpus_id, entity_type, db_pk,
        )
        self._cache[corpus_id] = db_pk

    async def require(self, corpus_id: str) -> UUID:
        pk = await self.get(corpus_id)
        if pk is None:
            raise KeyError(f"unknown corpus_id: {corpus_id!r} (run {self.run_id})")
        return pk
