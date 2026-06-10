"""Fixtures for the Signal mock fidelity suite (signal-cli method contract).

Seeds a deterministic install (linked-device session + self identity) plus a small,
controlled set of threads/messages with known timestamps so the backward-walk,
self-sent (out=True) and group/direct shape assertions are exact:

  - group  "eng"      (base64 groupId GROUP_ENG): ts 1..5, msg 3 self-sent (out).
  - group  "founders" (base64 groupId GROUP_FND): ts 1..3, msg 2 self-sent.
  - direct "dm"       (peer uuid DM_PEER): ts 1..3, msg 2 self-sent (out, no sender).

A message id IS its ``timestamp`` in MILLISECONDS (Signal has no integer id).
Provides an httpx ASGI client for the HTTP method surface + the in-memory
ASGIWebSocketDriver (reused from discord) for the live receive gateway.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from spammers.signal.seed import _group_id, _signal_uuid

SESSION = "test-session"
ACCOUNT_LABEL = "testlabel"
SELF_NUMBER = "+15550100777"
SELF_UUID = _signal_uuid("selfuser")
SELF_USERNAME = "selfuser"
HOST = "mock"

GROUP_ENG = _group_id("eng")
GROUP_FND = _group_id("founders")
DM_PEER = _signal_uuid("dana")
ALICE = _signal_uuid("alice")
BOB = _signal_uuid("bob")

# 2026-06-01T00:00:00Z epoch MILLISECONDS + 1h steps.
_T0 = 1748736000000
_H = 3600000

# thread (kind, thread_id, title)
THREADS = [
    ("group", GROUP_ENG, "eng"),
    ("group", GROUP_FND, "founders"),
    ("direct", DM_PEER, "Dana DM"),
]
# messages: (thread_id, ts_ms, sender_uuid, sender_number, sender_name, body, out, rev)
MESSAGES = [
    # group "eng" — ts 1..5, msg 3 self-sent (out=True, no sender).
    (GROUP_ENG, _T0 + 0 * _H, ALICE, "+15551110001", "Alice", "first", False, 3),
    (GROUP_ENG, _T0 + 1 * _H, BOB, "+15551110002", "Bob", "second", False, 3),
    (GROUP_ENG, _T0 + 2 * _H, None, None, None, "third (self)", True, 3),
    (GROUP_ENG, _T0 + 3 * _H, ALICE, "+15551110001", "Alice", "fourth", False, 3),
    (GROUP_ENG, _T0 + 4 * _H, BOB, "+15551110002", "Bob", "fifth", False, 3),
    # group "founders".
    (GROUP_FND, _T0 + 0 * _H, ALICE, "+15551110001", "Alice", "hi founders", False, 2),
    (GROUP_FND, _T0 + 1 * _H, None, None, None, "agenda? (self)", True, 2),
    (GROUP_FND, _T0 + 2 * _H, BOB, "+15551110002", "Bob", "see doc", False, 2),
    # direct "dm" — msg 2 self-sent (out=True, no sender).
    (DM_PEER, _T0 + 0 * _H, DM_PEER, "+15551110009", "Dana", "hey", False, None),
    (DM_PEER, _T0 + 1 * _H, None, None, None, "yo (self)", True, None),
    (DM_PEER, _T0 + 2 * _H, DM_PEER, "+15551110009", "Dana", "later", False, None),
]


@pytest_asyncio.fixture(loop_scope="session")
async def signal_setup(pool):
    """Insert a run + the controlled install/threads/messages. Returns (run_id, install_pk)."""
    run_id = uuid4()
    await pool.execute(
        """INSERT INTO org.runs (id, size, runtime, seed, fyralis_tenant_id,
               fyralis_base_url, virtual_now, mode, speed_multiplier)
           VALUES ($1,'small','few_months',7,$2,'http://localhost:8000',
                   '2026-06-02T00:00:00Z','frozen',1.0)""",
        run_id, uuid4())
    # A team + people so inject_signal_message can resolve a sender/actor
    # (timeline.events.actor_id is NOT NULL); the self account == 'selfuser'.
    team_pk = uuid4()
    await pool.execute(
        "INSERT INTO org.teams (id, run_id, name) VALUES ($1,$2,'Engineering')",
        team_pk, run_id)
    for handle, name in (("selfuser", "Self User"), ("alice", "Alice A"),
                         ("bob", "Bob B")):
        await pool.execute(
            """INSERT INTO org.people (id, run_id, handle, full_name, email, role,
                   level, team_id, timezone, started_at)
               VALUES ($1,$2,$3,$4,$5,'engineer','mid',$6,'UTC','2026-06-02T00:00:00Z')""",
            uuid4(), run_id, handle, name, f"{handle}@test.com", team_pk)
    inst_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_signal.installations
            (id, run_id, account_label, session_string, account_number, account_uuid,
             account_username, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,now())""",
        inst_pk, run_id, ACCOUNT_LABEL, SESSION, SELF_NUMBER, SELF_UUID, SELF_USERNAME)
    thr_pk: dict[str, UUID] = {}
    for kind, tid, title in THREADS:
        pk = uuid4()
        thr_pk[tid] = pk
        await pool.execute(
            """INSERT INTO app_signal.threads
                (id, install_pk, thread_id, thread_kind, thread_title, created_at)
               VALUES ($1,$2,$3,$4,$5,now())""",
            pk, inst_pk, tid, kind, title)
    for (tid, ts_ms, s_uuid, s_num, s_name, body, out, rev) in MESSAGES:
        await pool.execute(
            """INSERT INTO app_signal.messages
                (id, thread_pk, ts_ms, sender_uuid, sender_number, sender_name,
                 body, out, group_revision, created_at, is_historical)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,TRUE)""",
            uuid4(), thr_pk[tid], ts_ms, s_uuid, s_num, s_name, body, out, rev,
            datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc))
    return run_id, inst_pk


@pytest_asyncio.fixture(loop_scope="session")
async def sig_client(pool, signal_setup):
    """httpx ASGI client + the state singleton wired to the controlled run."""
    from spammers.signal import state as s_state
    from spammers.signal.app import create_app
    from spammers.signal.gateway import SessionHub

    run_id, _ = signal_setup
    s_state._STATE = s_state.SignalMockState(pool=pool, run_id=run_id, hub=SessionHub())
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url=f"http://{HOST}") as c:
        yield c
    s_state._STATE = None


def auth_headers(session: str = SESSION) -> dict[str, str]:
    return {"Authorization": f"Session {session}"}
