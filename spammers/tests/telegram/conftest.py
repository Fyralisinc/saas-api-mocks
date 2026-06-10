"""Fixtures for the Telegram mock fidelity suite (MTProto method contract).

Seeds a deterministic install (session credential + self user) plus a small,
controlled set of dialogs/messages with known ids/dates so the backward-walk,
edit, and no-from_id assertions are exact:

  - channel "eng"      (access_hash AH_ENG): message ids 1..5, msg 3 edited.
  - channel "ann"      (broadcast): ids 1..2, NO from_id (channel posts).
  - chat    "founders" (NO access_hash — basic group): ids 1..3.
  - user    "dm"       (access_hash AH_DM): ids 1..3, msg 2 self-sent (NO from_id, out).

Provides an httpx ASGI client for the HTTP method surface + the in-memory
ASGIWebSocketDriver for the live updates gateway (httpx's transport can't speak
WebSocket; the driver runs the endpoint on the current loop, sharing the pool).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

SESSION = "test-session"
ACCOUNT_LABEL = "testlabel"
SELF_USER_ID = 777
SELF_USERNAME = "selfuser"
HOST = "mock"

AH_ENG = 5_000_000_001
AH_DM = 5_000_000_002

# 2026-06-01T00:00:00Z epoch seconds + 1h steps.
_T0 = 1748736000
_H = 3600

# dialog (kind, dialog_id, access_hash, title)
DIALOGS = [
    ("channel", 1001, AH_ENG, "eng"),
    ("channel", 1002, None, "ann"),       # broadcast (access_hash None for the test)
    ("chat", 2001, None, "founders"),     # basic group: NO access_hash
    ("user", 3001, AH_DM, "dm"),
]
# messages: (dialog_id, message_id, date_ts, edit_date_ts, text, out, from_user_id)
MESSAGES = [
    # channel "eng" — ids 1..5, msg 3 edited.
    (1001, 1, _T0 + 0 * _H, None, "first", False, 9001),
    (1001, 2, _T0 + 1 * _H, None, "second", False, 9002),
    (1001, 3, _T0 + 2 * _H, _T0 + 2 * _H + 600, "third (edited)", False, 9001),
    (1001, 4, _T0 + 3 * _H, None, "fourth", True, SELF_USER_ID),
    (1001, 5, _T0 + 4 * _H, None, "fifth", False, 9003),
    # channel "ann" broadcast — NO from_id.
    (1002, 1, _T0 + 0 * _H, None, "announcement one", False, None),
    (1002, 2, _T0 + 5 * _H, None, "announcement two", False, None),
    # chat "founders".
    (2001, 1, _T0 + 0 * _H, None, "hi founders", False, 9001),
    (2001, 2, _T0 + 1 * _H, None, "agenda?", False, 9002),
    (2001, 3, _T0 + 2 * _H, None, "see doc", True, SELF_USER_ID),
    # user "dm" — msg 2 self-sent (NO from_id, out=True).
    (3001, 1, _T0 + 0 * _H, None, "hey", False, 3001),
    (3001, 2, _T0 + 1 * _H, None, "yo (self)", True, None),
    (3001, 3, _T0 + 2 * _H, None, "later", False, 3001),
]


@pytest_asyncio.fixture(loop_scope="session")
async def telegram_setup(pool):
    """Insert a run + the controlled install/dialogs/messages. Returns (run_id, install_pk)."""
    run_id = uuid4()
    await pool.execute(
        """INSERT INTO org.runs (id, size, runtime, seed, fyralis_tenant_id,
               fyralis_base_url, virtual_now, mode, speed_multiplier)
           VALUES ($1,'small','few_months',7,$2,'http://localhost:8000',
                   '2026-06-02T00:00:00Z','frozen',1.0)""",
        run_id, uuid4())
    # A team + people so inject_telegram_message can resolve a sender/actor
    # (timeline.events.actor_id is NOT NULL); the self account == 'selfuser'.
    team_pk = uuid4()
    await pool.execute(
        "INSERT INTO org.teams (id, run_id, name) VALUES ($1,$2,'Engineering')",
        team_pk, run_id)
    for handle, name in (("selfuser", "Self User"), ("teammate1", "Team Mate One"),
                         ("teammate2", "Team Mate Two")):
        await pool.execute(
            """INSERT INTO org.people (id, run_id, handle, full_name, email, role,
                   level, team_id, timezone, started_at)
               VALUES ($1,$2,$3,$4,$5,'engineer','mid',$6,'UTC','2026-06-02T00:00:00Z')""",
            uuid4(), run_id, handle, name, f"{handle}@test.com", team_pk)
    inst_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_telegram.installations
            (id, run_id, account_label, session_string, api_id, api_hash,
             self_user_id, self_username, self_phone, created_at)
           VALUES ($1,$2,$3,$4,'2040','hash',$5,$6,'+15550000001',now())""",
        inst_pk, run_id, ACCOUNT_LABEL, SESSION, SELF_USER_ID, SELF_USERNAME)
    dlg_pk: dict[int, UUID] = {}
    for kind, did, ah, title in DIALOGS:
        pk = uuid4()
        dlg_pk[did] = pk
        await pool.execute(
            """INSERT INTO app_telegram.dialogs
                (id, install_pk, dialog_id, dialog_kind, access_hash, title, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,now())""",
            pk, inst_pk, did, kind, ah, title)
    for (did, mid, date_ts, edit_ts, text, out, from_uid) in MESSAGES:
        await pool.execute(
            """INSERT INTO app_telegram.messages
                (id, dialog_pk, message_id, date_ts, edit_date_ts, text, out,
                 from_user_id, created_at, is_historical)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,TRUE)""",
            uuid4(), dlg_pk[did], mid, date_ts, edit_ts, text, out, from_uid,
            datetime.fromtimestamp(date_ts, tz=timezone.utc))
    return run_id, inst_pk


@pytest_asyncio.fixture(loop_scope="session")
async def tg_client(pool, telegram_setup):
    """httpx ASGI client + the state singleton wired to the controlled run."""
    from spammers.telegram import state as t_state
    from spammers.telegram.app import create_app
    from spammers.telegram.gateway import SessionHub

    run_id, _ = telegram_setup
    t_state._STATE = t_state.TelegramMockState(pool=pool, run_id=run_id, hub=SessionHub())
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url=f"http://{HOST}") as c:
        yield c
    t_state._STATE = None


def auth_headers(session: str = SESSION) -> dict[str, str]:
    return {"Authorization": f"Session {session}"}
