"""Gateway frame builders.

Only op-0 DISPATCH frames carry a non-null ``s`` (sequence) and ``t`` (event
name); every other op sends ``s: null, t: null``. Dispatch frames are built via
:meth:`GatewaySession.dispatch` (which stamps the seq + buffers for RESUME), so
the builders here cover the control opcodes only.
"""
from __future__ import annotations

from typing import Any, Optional

from spammers.discord.gateway.opcodes import HEARTBEAT_INTERVAL_MS, Op


def _control(op: Op, d: Any) -> dict[str, Any]:
    return {"op": int(op), "d": d, "s": None, "t": None}


def hello(heartbeat_interval: int = HEARTBEAT_INTERVAL_MS) -> dict[str, Any]:
    return _control(Op.HELLO, {"heartbeat_interval": heartbeat_interval})


def heartbeat_ack() -> dict[str, Any]:
    return _control(Op.HEARTBEAT_ACK, None)


def heartbeat_request(last_seq: Optional[int]) -> dict[str, Any]:
    return _control(Op.HEARTBEAT, last_seq)


def reconnect() -> dict[str, Any]:
    return _control(Op.RECONNECT, None)


def invalid_session(resumable: bool) -> dict[str, Any]:
    # d is a boolean indicating whether the session may be resumed.
    return _control(Op.INVALID_SESSION, resumable)
