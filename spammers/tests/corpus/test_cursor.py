"""Cursor advance is monotone; never moves backward."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from spammers.corpus import cursor as cursor_mod

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_cursor_starts_null(pool, run_id):
    assert await cursor_mod.get(pool, run_id) is None


async def test_advance_sets_when_null(pool, run_id):
    t = datetime(2024, 6, 1, tzinfo=timezone.utc)
    got = await cursor_mod.advance(pool, run_id, t)
    assert got == t
    assert await cursor_mod.get(pool, run_id) == t


async def test_advance_moves_forward(pool, run_id):
    t1 = datetime(2024, 6, 1, tzinfo=timezone.utc)
    t2 = datetime(2024, 9, 1, tzinfo=timezone.utc)
    await cursor_mod.set_(pool, run_id, t1)
    got = await cursor_mod.advance(pool, run_id, t2)
    assert got == t2


async def test_advance_refuses_backward(pool, run_id):
    t_late = datetime(2024, 9, 1, tzinfo=timezone.utc)
    t_early = datetime(2024, 6, 1, tzinfo=timezone.utc)
    await cursor_mod.set_(pool, run_id, t_late)
    got = await cursor_mod.advance(pool, run_id, t_early)
    assert got == t_late, "advance must not go backward"
