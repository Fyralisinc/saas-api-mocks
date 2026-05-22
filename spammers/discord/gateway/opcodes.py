"""Discord Gateway opcodes, close codes, and gateway intents (v10).

Values match the real Gateway so a real client library (discord.py, discord.js)
talks to the mock unmodified.
"""
from __future__ import annotations

from enum import IntEnum


class Op(IntEnum):
    DISPATCH = 0           # recv — an event was dispatched (carries s + t)
    HEARTBEAT = 1          # send/recv — keepalive
    IDENTIFY = 2           # send — start a new session
    PRESENCE_UPDATE = 3    # send — update presence (no-op here)
    VOICE_STATE = 4        # send — join/leave/move voice (no-op here)
    RESUME = 6             # send — resume a dropped session
    RECONNECT = 7          # recv — server asks the client to reconnect + resume
    REQUEST_GUILD_MEMBERS = 8  # send — request members (no-op here)
    INVALID_SESSION = 9    # recv — session invalidated (d=resumable?)
    HELLO = 10             # recv — sent on connect with heartbeat_interval
    HEARTBEAT_ACK = 11     # recv — acks a client HEARTBEAT


class CloseCode(IntEnum):
    UNKNOWN_ERROR = 4000
    UNKNOWN_OPCODE = 4001
    DECODE_ERROR = 4002
    NOT_AUTHENTICATED = 4003       # sent payload before IDENTIFY
    AUTHENTICATION_FAILED = 4004   # bad token
    ALREADY_AUTHENTICATED = 4005   # >1 IDENTIFY
    INVALID_SEQ = 4007             # bad seq on RESUME
    RATE_LIMITED = 4008
    SESSION_TIMED_OUT = 4009       # missed heartbeats
    INVALID_INTENTS = 4013
    DISALLOWED_INTENTS = 4014


class Intents:
    """Gateway intent bit flags (subset we model)."""

    GUILDS = 1 << 0
    GUILD_MEMBERS = 1 << 1
    GUILD_MESSAGES = 1 << 9
    DIRECT_MESSAGES = 1 << 12
    MESSAGE_CONTENT = 1 << 15

    # Privileged intents must be toggled in the Developer Portal; requesting one
    # the application isn't approved for closes the connection (4014). The mock
    # treats GUILD_MEMBERS, MESSAGE_CONTENT (and presences) as privileged.
    PRIVILEGED = GUILD_MEMBERS | MESSAGE_CONTENT

    # Every bit defined across the real gateway (v10) occupies bits 0..24. Bits
    # above that are invalid and close the connection (4013).
    ALL_KNOWN = (1 << 25) - 1


HEARTBEAT_INTERVAL_MS = 41250
