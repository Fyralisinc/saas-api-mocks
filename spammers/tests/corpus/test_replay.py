"""End-to-end: backfill the tiny corpus, verify org rows + idmap populated."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from spammers.corpus import replay
from spammers.corpus.idmap import IdMap

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_backfill_creates_people_and_teams(pool, run_id, tiny_corpus):
    until = datetime(2024, 12, 31, tzinfo=timezone.utc)
    counts = await replay.backfill(pool, run_id, tiny_corpus, until=until)

    assert counts["org.team.create"] == 2
    assert counts["org.person.create"] == 2

    people = await pool.fetch(
        "SELECT handle, full_name, role FROM org.people WHERE run_id = $1 ORDER BY handle",
        run_id,
    )
    assert [r["handle"] for r in people] == ["alice", "bob"]

    teams = await pool.fetch(
        "SELECT name, parent_id FROM org.teams WHERE run_id = $1 ORDER BY name",
        run_id,
    )
    assert [r["name"] for r in teams] == ["Protocol", "Research"]
    # Research's parent should be Protocol's pk.
    protocol = next(t for t in teams if t["name"] == "Protocol")
    research = next(t for t in teams if t["name"] == "Research")
    assert research["parent_id"] is not None
    assert protocol["parent_id"] is None


async def test_backfill_populates_idmap(pool, run_id, tiny_corpus):
    until = datetime(2024, 12, 31, tzinfo=timezone.utc)
    await replay.backfill(pool, run_id, tiny_corpus, until=until)

    idmap = IdMap(pool, run_id)
    await idmap.warm()
    assert await idmap.get("person:alice") is not None
    assert await idmap.get("team:protocol") is not None
    assert await idmap.get("person:nobody") is None


async def test_backfill_is_idempotent(pool, run_id, tiny_corpus):
    until = datetime(2024, 12, 31, tzinfo=timezone.utc)
    await replay.backfill(pool, run_id, tiny_corpus, until=until)
    # Second pass — should not duplicate rows.
    await replay.backfill(pool, run_id, tiny_corpus, until=until)
    n_people = await pool.fetchval(
        "SELECT count(*) FROM org.people WHERE run_id = $1", run_id,
    )
    assert n_people == 2


async def test_dm_membership_includes_receive_only_participant(pool, run_id, tiny_corpus, tmp_path):
    # alice & bob exist in the tiny corpus. Add a 1:1 DM where ONLY alice posts;
    # bob is a receive-only participant. Real Slack still shows bob the DM under
    # his user token, so channel_membership must carry bob even though he never
    # authored a message — proving membership is seeded from participants, not
    # derived from authorship.
    lines = tiny_corpus.read_text().splitlines()
    lines += [
        json.dumps({"t": "2024-03-01T09:00:00Z", "provider": "slack", "kind": "channel.create",
                    "actor": "person:alice",
                    "payload": {"id": "dm:alice~bob", "name": "dm:alice~bob",
                                "is_im": True, "is_private": True,
                                "participants": ["person:alice", "person:bob"]}}),
        json.dumps({"t": "2024-03-01T09:01:00Z", "provider": "slack", "kind": "message",
                    "actor": "person:alice",
                    "payload": {"channel": "dm:alice~bob", "text": "hi bob"}}),
    ]
    corpus = tmp_path / "dm.events.jsonl"
    corpus.write_text("\n".join(lines) + "\n")

    until = datetime(2024, 12, 31, tzinfo=timezone.utc)
    await replay.backfill(pool, run_id, corpus, until=until)

    members = await pool.fetch(
        """
        SELECT p.handle
          FROM app_slack.channel_membership cm
          JOIN app_slack.channels c ON c.id = cm.channel_pk
          JOIN app_slack.workspaces w ON w.id = c.workspace_id
          JOIN app_slack.users u ON u.id = cm.user_pk
          JOIN org.people p ON p.id = u.person_id
         WHERE w.run_id = $1 AND c.is_im
        """,
        run_id,
    )
    assert {r["handle"] for r in members} == {"alice", "bob"}


async def test_backfill_partial_until(pool, run_id, tiny_corpus):
    """A cursor before person.create lands teams only."""
    cutoff = datetime(2024, 1, 15, 9, 0, 1, tzinfo=timezone.utc)
    counts = await replay.backfill(pool, run_id, tiny_corpus, until=cutoff)
    assert counts.get("org.team.create") == 2
    assert "org.person.create" not in counts
    n_people = await pool.fetchval(
        "SELECT count(*) FROM org.people WHERE run_id = $1", run_id,
    )
    assert n_people == 0
