"""Shared fixtures for the Slack-mock fidelity suite.

The mock is DB-backed (asyncpg → SPAMMERS_DB_URL). These fixtures:

  1. Point the mock at a dedicated, auto-created test DB.
  2. Apply the migration and insert a small, *hand-built deterministic*
     dataset (one workspace, three users, four channels, a threaded
     conversation in #general). Contract assertions need exact, known
     ts/counts, so we hand-seed rather than replay the Gharelu-Alpen corpus.
  3. Wire the Slack ``state`` singleton to that pool + run, then expose an
     in-process ASGI client (httpx ASGITransport — note it does NOT run
     FastAPI lifespan events, so we set state ourselves).

Test DB URL precedence: ``SPAMMERS_TEST_DB_URL`` env, else the documented
default with the db name swapped to ``spammers_test``.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


# --------------------------------------------------------------------------
# Known, deterministic identifiers — assertions reference these directly.
# --------------------------------------------------------------------------
TEAM_ID = "T0FIDELITY"
TEAM_NAME = "Fidelity Test Co"
TEAM_DOMAIN = "fidelity-test"
APP_ID = "A0APP00001"
BOT_USER_ID = "U0BOTUSER0"
BOT_TOKEN = "xoxb-0000000000-1111111111-fidelitytoken00000000000"
CLIENT_ID = "111111111.222222222"
CLIENT_SECRET = "fidelity-client-secret"
SIGNING_SECRET = "abcdef0123456789abcdef0123456789"

USER_ALICE = "U0ALICE001"
USER_BOB = "U0BOB00002"
USER_CAROL = "U0CAROL003"

CH_GENERAL = "C0GENERAL0"
CH_RANDOM = "C0RANDOM01"
CH_PRIVATE = "C0PRIVATE0"
CH_ARCHIVED = "C0ARCHIVE0"
CH_LOCKED = "C0LOCKED00"     # public channel the bot has NOT joined (bot_is_member=FALSE)
CH_DM_AB = "D0ALICEBOB"      # 1:1 DM between alice & bob
CH_MPIM_ABC = "G0ABCGROUP"   # group DM among alice, bob, carol

# Per-user xoxp token (alice consented to DM ingestion).
ALICE_USER_TOKEN = "xoxp-0000000000-2222222222-alicedmtoken000000000000"
ALICE_USER_SCOPES = ["im:read", "im:history", "mpim:read", "mpim:history", "users:read"]

# #general message timeline (epoch base, microsecond precision in ts).
_BASE = 1768000000
TS_M1 = f"{_BASE + 100}.000100"          # root, alice
TS_M2 = f"{_BASE + 200}.000100"          # root, bob
TS_PARENT = f"{_BASE + 300}.000100"      # thread parent, alice (reply_count=2)
TS_R1 = f"{_BASE + 310}.000100"          # reply, bob
TS_R2 = f"{_BASE + 320}.000100"          # reply, carol
GENERAL_ROOT_TS = [TS_M1, TS_M2, TS_PARENT]      # what history should return
GENERAL_ROOT_TS_DESC = [TS_PARENT, TS_M2, TS_M1]  # newest-first order

# DM message timeline (im + mpim).
TS_DM1 = f"{_BASE + 400}.000100"   # alice -> bob (im)
TS_DM2 = f"{_BASE + 410}.000100"   # bob -> alice (im)
TS_MP1 = f"{_BASE + 500}.000100"   # alice (mpim)
TS_MP2 = f"{_BASE + 510}.000100"   # carol (mpim)

VIRTUAL_NOW = datetime.fromtimestamp(_BASE + 1_000_000, tz=timezone.utc)


def test_db_url() -> str:
    explicit = os.environ.get("SPAMMERS_TEST_DB_URL")
    if explicit:
        return explicit
    base = os.environ.get(
        "SPAMMERS_DB_URL", "postgresql://postgres:postgres@localhost:5432/mock_orgs"
    )
    head, _ = base.rsplit("/", 1)
    return f"{head}/spammers_test"


# Make every module that reads SPAMMERS_DB_URL (db.py, state.py) hit the test DB.
os.environ["SPAMMERS_DB_URL"] = test_db_url()


_SCHEMAS = ["timeline", "app_slack", "app_discord", "app_github", "app_gmail",
            "app_calendar", "app_notion", "app_drive", "app_jira", "app_quickbooks",
            "app_grafana", "app_mercury", "app_ashby", "app_brex", "app_deel",
            "app_hibob", "app_figma", "app_miro", "app_ramp", "app_gusto",
            "oauth", "org"]


async def _reset_schemas(pool) -> None:
    for s in _SCHEMAS:
        await pool.execute(f"DROP SCHEMA IF EXISTS {s} CASCADE")


async def _seed(pool) -> UUID:
    """Insert the deterministic fixture dataset. Returns the run_id."""
    run_id = uuid4()
    ws_pk = uuid4()
    team_pk = uuid4()
    await pool.execute(
        """
        INSERT INTO org.runs (id, size, runtime, seed, fyralis_tenant_id,
                              fyralis_base_url, virtual_now, mode, speed_multiplier)
        VALUES ($1, 'small', 'few_months', 1, $2, 'http://localhost:8000', $3, 'frozen', 1.0)
        """,
        run_id, uuid4(), VIRTUAL_NOW,
    )
    await pool.execute(
        "INSERT INTO org.teams (id, run_id, name) VALUES ($1, $2, 'Engineering')",
        team_pk, run_id,
    )

    people = [
        ("alice", "Alice Anderson", "alice@fidelity-test.com", USER_ALICE),
        ("bob", "Bob Brown", "bob@fidelity-test.com", USER_BOB),
        ("carol", "Carol Clark", "carol@fidelity-test.com", USER_CAROL),
    ]
    person_pks: dict[str, UUID] = {}
    for handle, full_name, email, _slack_id in people:
        pid = uuid4()
        person_pks[handle] = pid
        await pool.execute(
            """
            INSERT INTO org.people (id, run_id, handle, full_name, email, role, level,
                                    team_id, timezone, started_at)
            VALUES ($1, $2, $3, $4, $5, 'engineer', 'mid', $6, 'America/Los_Angeles', $7)
            """,
            pid, run_id, handle, full_name, email, team_pk, VIRTUAL_NOW,
        )

    await pool.execute(
        """
        INSERT INTO app_slack.workspaces
            (id, run_id, team_id, team_name, team_domain, signing_secret,
             client_id, client_secret, bot_token, bot_user_id, app_id, app_distribution)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'non_marketplace')
        """,
        ws_pk, run_id, TEAM_ID, TEAM_NAME, TEAM_DOMAIN, SIGNING_SECRET,
        CLIENT_ID, CLIENT_SECRET, BOT_TOKEN, BOT_USER_ID, APP_ID,
    )

    user_pks: dict[str, UUID] = {}
    for handle, full_name, _email, slack_id in people:
        upk = uuid4()
        user_pks[slack_id] = upk
        await pool.execute(
            """
            INSERT INTO app_slack.users (id, workspace_id, person_id, slack_user_id, profile)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            """,
            upk, ws_pk, person_pks[handle], slack_id, '{"title": "Engineer"}',
        )

    # (cid, name, is_private, is_archived, is_general, is_im, is_mpim, bot_is_member)
    channels = [
        (CH_GENERAL, "general", False, False, True, False, False, True),
        (CH_RANDOM, "random", False, False, False, False, False, True),
        (CH_PRIVATE, "secret-plans", True, False, False, False, False, True),
        (CH_ARCHIVED, "old-stuff", False, True, False, False, False, True),
        (CH_LOCKED, "locked-room", False, False, False, False, False, False),
        (CH_DM_AB, "dm-alice-bob", True, False, False, True, False, True),
        (CH_MPIM_ABC, "mpim-abc", True, False, False, False, True, True),
    ]
    chan_pks: dict[str, UUID] = {}
    for cid, name, is_private, is_archived, is_general, is_im, is_mpim, bot_member in channels:
        cpk = uuid4()
        chan_pks[cid] = cpk
        await pool.execute(
            """
            INSERT INTO app_slack.channels
                (id, workspace_id, channel_id, name, is_private, is_archived,
                 is_general, is_im, is_mpim, bot_is_member,
                 topic, purpose, creator_user_id, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
            """,
            cpk, ws_pk, cid, name, is_private, is_archived, is_general,
            is_im, is_mpim, bot_member,
            f"{name} topic", f"{name} purpose", USER_ALICE, VIRTUAL_NOW,
        )

    # Memberships: #general (all three), the im (alice+bob), the mpim (all three).
    memberships = {
        CH_GENERAL: (USER_ALICE, USER_BOB, USER_CAROL),
        CH_DM_AB: (USER_ALICE, USER_BOB),
        CH_MPIM_ABC: (USER_ALICE, USER_BOB, USER_CAROL),
    }
    for cid, member_ids in memberships.items():
        for slack_id in member_ids:
            await pool.execute(
                "INSERT INTO app_slack.channel_membership (channel_pk, user_pk, joined_at) "
                "VALUES ($1, $2, $3)",
                chan_pks[cid], user_pks[slack_id], VIRTUAL_NOW,
            )

    # alice's xoxp user token (the DM consent row).
    await pool.execute(
        """
        INSERT INTO app_slack.user_tokens (id, workspace_id, slack_user_id, user_token, scopes)
        VALUES ($1, $2, $3, $4, $5::jsonb)
        """,
        uuid4(), ws_pk, USER_ALICE, ALICE_USER_TOKEN, json.dumps(ALICE_USER_SCOPES),
    )

    # #general messages: two roots, a thread parent (reply_count=2), two replies.
    # The parent carries the reply roll-up fields real Slack returns.
    msgs = [
        (TS_M1, None, USER_ALICE, "first message", 0, 0, None, []),
        (TS_M2, None, USER_BOB, "second message", 0, 0, None, []),
        (TS_PARENT, None, USER_ALICE, "thread parent", 2, 2, TS_R2, [USER_BOB, USER_CAROL]),
        (TS_R1, TS_PARENT, USER_BOB, "reply one", 0, 0, None, []),
        (TS_R2, TS_PARENT, USER_CAROL, "reply two", 0, 0, None, []),
        # DM (im) messages between alice & bob.
        (TS_DM1, None, USER_ALICE, "hey bob (dm)", 0, 0, None, []),
        (TS_DM2, None, USER_BOB, "hey alice (dm)", 0, 0, None, []),
        # group DM (mpim) messages.
        (TS_MP1, None, USER_ALICE, "mpim from alice", 0, 0, None, []),
        (TS_MP2, None, USER_CAROL, "mpim from carol", 0, 0, None, []),
    ]
    msg_channel = {
        TS_M1: CH_GENERAL, TS_M2: CH_GENERAL, TS_PARENT: CH_GENERAL,
        TS_R1: CH_GENERAL, TS_R2: CH_GENERAL,
        TS_DM1: CH_DM_AB, TS_DM2: CH_DM_AB,
        TS_MP1: CH_MPIM_ABC, TS_MP2: CH_MPIM_ABC,
    }
    for ts, thread_ts, user_slack, text, reply_count, ruc, latest, rusers in msgs:
        await pool.execute(
            """
            INSERT INTO app_slack.messages
                (id, channel_pk, user_pk, ts, thread_ts, text, reply_count,
                 reply_users_count, latest_reply, reply_users)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
            """,
            uuid4(), chan_pks[msg_channel[ts]], user_pks[user_slack], ts, thread_ts,
            text, reply_count, ruc, latest, json.dumps(rusers),
        )

    return run_id


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def pool():
    from spammers.common.db import apply_migrations, create_pool, ensure_database_exists

    await ensure_database_exists()
    p = await create_pool()
    await _reset_schemas(p)
    await apply_migrations(p)
    yield p
    await _reset_schemas(p)
    await p.close()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def run_id(pool) -> UUID:
    return await _seed(pool)


@pytest_asyncio.fixture(loop_scope="session")
async def client(pool, run_id):
    """In-process ASGI client with the Slack state singleton wired up.

    ASGITransport does not run FastAPI lifespan, so we set state directly and
    reset the rate limiter each test (in-process buckets would otherwise leak).
    """
    from spammers.common.rate_limit import RateLimiter
    from spammers.slack import state as slack_state
    from spammers.slack.app import create_app

    slack_state._STATE = slack_state.SlackMockState(
        pool=pool, run_id=run_id, rate_limiter=RateLimiter()
    )
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://mock") as c:
        yield c
    slack_state._STATE = None


@pytest.fixture
def auth_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {BOT_TOKEN}"}


@pytest.fixture
def user_auth_header() -> dict[str, str]:
    """alice's xoxp user token — reads her DMs (im) and group DMs (mpim)."""
    return {"Authorization": f"Bearer {ALICE_USER_TOKEN}"}


@pytest.fixture
def reset_rate_limit():
    """Clear the in-process rate-limit buckets — simulates a fresh time window.

    Needed for tests that deliberately make several sequential calls to a
    low-tier method (e.g. conversations.history is Tier 1: ~1/min, no burst).
    """
    from spammers.common.rate_limit import RateLimiter
    from spammers.slack import state as slack_state

    def _reset() -> None:
        slack_state.state().rate_limiter = RateLimiter()

    return _reset
