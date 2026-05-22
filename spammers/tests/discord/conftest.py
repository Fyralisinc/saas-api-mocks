"""Fixtures for the Discord-mock fidelity suite.

Reuses the session ``pool`` from the top-level conftest, seeds a deterministic
Discord application (with a known Ed25519 keypair so tests can verify signed
interactions and authenticate the Gateway), one guild, two channels, two users,
and three messages, then wires the Discord ``state`` singleton (pool + run +
SessionHub) and exposes an ASGI HTTP client plus the app for the WebSocket
driver.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from spammers.common.signing import generate_ed25519_keypair

# Deterministic identifiers the tests assert against.
APPLICATION_ID = "900000000000000001"
CLIENT_ID = APPLICATION_ID
CLIENT_SECRET = "test-client-secret"
BOT_TOKEN = "MOCK.bottoken.fidelitytestvalue000000"
GUILD_ID = "900000000000000010"
CHANNEL_GENERAL = "900000000000000020"
CHANNEL_OFFTOPIC = "900000000000000021"
USER_ALICE = "900000000000000100"
USER_BOB = "900000000000000101"

# Three messages in #general (realistic, ordered 2020-era snowflakes; these are
# smaller than a snowflake minted at VIRTUAL_NOW, so a freshly-created message
# sorts newest). newest = MSG3.
MSG1 = "716600000000000001"
MSG2 = "716600000000000002"
MSG3 = "716600000000000003"
GENERAL_NEWEST_FIRST = [MSG3, MSG2, MSG1]

VIRTUAL_NOW = datetime(2020, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

_PRIVATE_HEX, _PUBLIC_HEX = generate_ed25519_keypair()

# NB: tests that need the application's public key read it from the DB (not these
# module constants) — the conftest is imported twice (plugin + explicit import),
# so a module-level keypair would differ from the one this fixture seeded.


@pytest_asyncio.fixture(loop_scope="session")
async def dc_run(pool) -> UUID:
    # Function-scoped: each test gets a fresh run_id + isolated dataset, so
    # write/dispatch tests don't pollute the deterministic read fixtures. (The
    # conftest is loaded twice — as a pytest plugin and via the explicit module
    # import in test files — so module-level keypairs differ between instances;
    # tests that need the key read it from the DB this fixture seeded.)
    run_id = uuid4()
    await pool.execute(
        """
        INSERT INTO org.runs (id, size, runtime, seed, fyralis_tenant_id,
                              fyralis_base_url, virtual_now, mode, speed_multiplier)
        VALUES ($1, 'small', 'few_months', 7, $2, 'http://localhost:8000', $3, 'frozen', 1.0)
        """,
        run_id, uuid4(), VIRTUAL_NOW,
    )
    alice_pk, bob_pk = uuid4(), uuid4()
    await pool.execute(
        """
        INSERT INTO org.people (id, run_id, handle, full_name, email, role, level, timezone, started_at)
        VALUES ($1, $2, 'alice', 'Alice Adams', 'alice@acme.test', 'engineer', 'senior', 'UTC', $3),
               ($4, $2, 'bob', 'Bob Brown', 'bob@acme.test', 'engineer', 'mid', 'UTC', $3)
        """,
        alice_pk, run_id, VIRTUAL_NOW - timedelta(days=30), bob_pk,
    )

    app_pk = uuid4()
    await pool.execute(
        """
        INSERT INTO app_discord.applications
            (id, run_id, application_id, client_id, client_secret, bot_token, public_key, private_key)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        app_pk, run_id, APPLICATION_ID, CLIENT_ID, CLIENT_SECRET, BOT_TOKEN,
        _PUBLIC_HEX, _PRIVATE_HEX,
    )
    guild_pk = uuid4()
    await pool.execute(
        """
        INSERT INTO app_discord.guilds (id, application_pk, guild_id, name, owner_user_id, created_at)
        VALUES ($1, $2, $3, 'Fidelity Test Guild', $4, $5)
        """,
        guild_pk, app_pk, GUILD_ID, USER_ALICE, VIRTUAL_NOW - timedelta(days=40),
    )
    alice_u, bob_u = uuid4(), uuid4()
    await pool.execute(
        """
        INSERT INTO app_discord.users
            (id, application_pk, person_id, discord_user_id, username, discriminator, is_bot)
        VALUES ($1, $2, $3, $4, 'alice', '0', FALSE),
               ($5, $2, $6, $7, 'bob', '0', FALSE)
        """,
        alice_u, app_pk, alice_pk, USER_ALICE, bob_u, bob_pk, USER_BOB,
    )
    chan_general, chan_offtopic = uuid4(), uuid4()
    await pool.execute(
        """
        INSERT INTO app_discord.channels (id, guild_pk, channel_id, name, type, topic, created_at)
        VALUES ($1, $2, $3, 'general', 0, 'General discussion', $4),
               ($5, $2, $6, 'off-topic', 0, 'Off-topic', $4)
        """,
        chan_general, guild_pk, CHANNEL_GENERAL, VIRTUAL_NOW - timedelta(days=40),
        chan_offtopic, CHANNEL_OFFTOPIC,
    )
    base = VIRTUAL_NOW - timedelta(hours=2)
    for i, (mid, author) in enumerate([(MSG1, alice_u), (MSG2, bob_u), (MSG3, alice_u)]):
        await pool.execute(
            """
            INSERT INTO app_discord.messages
                (id, channel_pk, message_id, author_user_pk, content, type, created_at)
            VALUES ($1, $2, $3, $4, $5, 0, $6)
            """,
            uuid4(), chan_general, mid, author, f"message {i + 1}", base + timedelta(minutes=i),
        )
    return run_id


@pytest_asyncio.fixture(loop_scope="session")
async def dc_state(pool, dc_run):
    from spammers.common.rate_limit import RateLimiter
    from spammers.discord import state as dc
    from spammers.discord.gateway.hub import SessionHub

    dc._STATE = dc.DiscordMockState(
        pool=pool, run_id=dc_run, rate_limiter=RateLimiter(), hub=SessionHub(),
    )
    yield dc._STATE
    dc._STATE = None


@pytest_asyncio.fixture(loop_scope="session")
async def dc_client(dc_state):
    from spammers.discord.app import create_app

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://mock") as c:
        yield c


@pytest.fixture
def gateway_app(dc_state):
    """The ASGI app object (shares the wired _STATE) for the WebSocket driver."""
    from spammers.discord.app import create_app

    return create_app()


@pytest.fixture
def auth_header() -> dict[str, str]:
    return {"Authorization": f"Bot {BOT_TOKEN}"}


@pytest_asyncio.fixture(loop_scope="session")
async def dispatcher(dc_state):
    """A GatewayDispatcher bound to the wired hub. The poll loop is NOT started;
    tests drive ``_drain_once()`` directly for determinism."""
    from spammers.discord.gateway.dispatcher import GatewayDispatcher

    return GatewayDispatcher(
        dc_state.pool, dc_state.run_id, dc_state.hub, poll_interval_s=0.02,
    )
