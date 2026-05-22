"""A single Gateway session.

Holds the per-connection protocol state: the outbound queue (drained by the
writer task — the *only* code that may write to the websocket), a monotonic
sequence counter stamped on every dispatch, the RESUME ring buffer, the
negotiated intents, and heartbeat bookkeeping.

A session outlives a single websocket: on disconnect the connection layer parks
the session in the hub's ``resumable`` map; a later RESUME rebinds a fresh queue
(``rebind``) and replays buffered dispatches.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, Optional
from uuid import UUID

from spammers.discord.gateway.opcodes import Op

# Outbound queue depth before we consider a client too slow and drop it.
OUT_QUEUE_MAXSIZE = 256
# How many op-0 dispatches we retain for RESUME replay.
RING_BUFFER_SIZE = 512


class GatewaySession:
    def __init__(
        self,
        *,
        session_id: str,
        token: str,
        application_pk: UUID,
        application_id: str,
        intents: int,
    ) -> None:
        self.session_id = session_id
        self.token = token
        self.application_pk = application_pk
        self.application_id = application_id
        self.intents = intents

        self.seq = 0
        self.out: asyncio.Queue = asyncio.Queue(maxsize=OUT_QUEUE_MAXSIZE)
        self.ring: deque[tuple[int, dict[str, Any]]] = deque(maxlen=RING_BUFFER_SIZE)

        self.connected = True
        self.last_heartbeat = time.monotonic()
        self.resume_deadline: Optional[float] = None

        # Out-of-band close request (hub QueueFull drop, server RECONNECT, etc.).
        # The writer task waits on this alongside the outbound queue.
        self.close_code: Optional[int] = None
        self.close_requested = asyncio.Event()

    def request_close(self, code: int) -> None:
        """Ask the connection to close with ``code`` (does not touch the ws)."""
        if self.close_code is None:
            self.close_code = code
        self.close_requested.set()

    # ----- sequence + outbound -----

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def _build_dispatch(self, t: str, d: dict[str, Any]) -> dict[str, Any]:
        s = self.next_seq()
        payload = {"op": Op.DISPATCH.value, "d": d, "s": s, "t": t}
        self.ring.append((s, payload))  # retained for RESUME replay
        return payload

    def dispatch(self, t: str, d: dict[str, Any]) -> dict[str, Any]:
        """Build + enqueue an op-0 dispatch with a fresh seq; buffer for RESUME."""
        payload = self._build_dispatch(t, d)
        self.enqueue(payload)
        return payload

    def buffer_dispatch(self, t: str, d: dict[str, Any]) -> dict[str, Any]:
        """Build + buffer an op-0 dispatch WITHOUT enqueuing (the session is
        disconnected/parked); replayed if the client RESUMEs."""
        return self._build_dispatch(t, d)

    def enqueue(self, payload: dict[str, Any]) -> None:
        """Queue a raw frame for the writer. Raises QueueFull on a slow client."""
        self.out.put_nowait(payload)

    def enqueue_buffered(self, payload: dict[str, Any]) -> None:
        """Re-queue an already-sequenced dispatch (RESUME replay) without
        re-stamping seq or re-buffering it."""
        self.out.put_nowait(payload)

    # ----- RESUME -----

    def oldest_buffered_seq(self) -> Optional[int]:
        return self.ring[0][0] if self.ring else None

    def frames_after(self, seq: int) -> list[dict[str, Any]]:
        return [payload for (s, payload) in self.ring if s > seq]

    def rebind(self) -> None:
        """A new websocket is taking over this session — fresh outbound queue."""
        self.out = asyncio.Queue(maxsize=OUT_QUEUE_MAXSIZE)
        self.connected = True
        self.resume_deadline = None
        self.last_heartbeat = time.monotonic()

    # ----- heartbeat -----

    def touch_heartbeat(self) -> None:
        self.last_heartbeat = time.monotonic()
