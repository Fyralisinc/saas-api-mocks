"""The Gateway WebSocket URL the mock advertises.

Real bots fetch ``GET /gateway`` (or ``/gateway/bot``) and connect to the
returned ``url`` with ``?v=10&encoding=json`` appended. We point it back at this
mock's own ``/gateway`` websocket route; override with ``SPAMMERS_DISCORD_WS_BASE``
(e.g. when the mock is reached through a different host/port).
"""
from __future__ import annotations

import os

DEFAULT_WS_BASE = "ws://localhost:7002/gateway"


def gateway_ws_base() -> str:
    return os.environ.get("SPAMMERS_DISCORD_WS_BASE", DEFAULT_WS_BASE)
