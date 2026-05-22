"""Offline fidelity: id formats, Ed25519 signing round-trip, opcode/intent values.

No DB or app — pure unit checks that the building blocks match real Discord.
"""
from __future__ import annotations

from datetime import datetime, timezone

from spammers.common.ids import discord_bot_token, discord_snowflake
from spammers.common.signing import (
    discord_sign,
    discord_verify,
    generate_ed25519_keypair,
)
from spammers.discord.gateway.opcodes import HEARTBEAT_INTERVAL_MS, Intents, Op


def test_snowflake_is_numeric_and_time_ordered():
    early = discord_snowflake(datetime(2021, 1, 1, tzinfo=timezone.utc))
    late = discord_snowflake(datetime(2023, 1, 1, tzinfo=timezone.utc))
    assert early.isdigit() and late.isdigit()
    assert int(early) < int(late)  # snowflakes embed the timestamp in high bits
    assert 15 <= len(late) <= 20


def test_bot_token_shape():
    tok = discord_bot_token()
    parts = tok.split(".")
    assert len(parts) == 3  # <id>.<ts>.<hmac>
    assert all(parts)


def test_ed25519_sign_verify_roundtrip():
    private_hex, public_hex = generate_ed25519_keypair()
    ts = "1700000000"
    body = b'{"type":1}'
    sig = discord_sign(private_hex, ts, body)
    assert discord_verify(public_hex, sig, ts, body) is True
    # Wrong timestamp or body must fail (signature is over ts + body).
    assert discord_verify(public_hex, sig, "1700000001", body) is False
    assert discord_verify(public_hex, sig, ts, body + b" ") is False


def test_opcode_values_match_discord():
    assert Op.DISPATCH == 0
    assert Op.HEARTBEAT == 1
    assert Op.IDENTIFY == 2
    assert Op.RESUME == 6
    assert Op.RECONNECT == 7
    assert Op.INVALID_SESSION == 9
    assert Op.HELLO == 10
    assert Op.HEARTBEAT_ACK == 11


def test_intent_bits_match_discord():
    assert Intents.GUILDS == 1 << 0
    assert Intents.GUILD_MESSAGES == 1 << 9
    assert Intents.MESSAGE_CONTENT == 1 << 15
    assert HEARTBEAT_INTERVAL_MS == 41250
