"""In-memory ASGI WebSocket driver for the Gateway tests.

httpx's ASGITransport does not speak the WebSocket protocol, and
``starlette.testclient`` runs on its own thread/event loop (which would detach
the session-scoped asyncpg pool). This driver instead runs the app's WebSocket
endpoint as a coroutine on the *current* event loop, exchanging ASGI
``websocket.*`` messages through two queues — so the pool, hub, and dispatcher
all share one loop and stay compatible.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any, Optional


class WebSocketClosed(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"websocket closed: {code}")
        self.code = code


class ASGIWebSocketDriver:
    def __init__(self, app, *, path: str = "/gateway", query: str = "v=10&encoding=json") -> None:
        self._app = app
        self._to_app: asyncio.Queue = asyncio.Queue()    # driver -> app (receive)
        self._from_app: asyncio.Queue = asyncio.Queue()  # app -> driver (send)
        self._scope: dict[str, Any] = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "path": path,
            "raw_path": path.encode(),
            "query_string": query.encode(),
            "headers": [],
            "subprotocols": [],
            "client": ("testclient", 0),
            "server": ("mock", 7002),
            "scheme": "ws",
        }
        self._task: Optional[asyncio.Task] = None
        self.closed_code: Optional[int] = None

    async def __aenter__(self) -> "ASGIWebSocketDriver":
        self._task = asyncio.create_task(self._app(self._scope, self._recv, self._send))
        await self._to_app.put({"type": "websocket.connect"})
        msg = await asyncio.wait_for(self._from_app.get(), timeout=2.0)
        if msg["type"] == "websocket.close":
            self.closed_code = msg.get("code")
            raise WebSocketClosed(self.closed_code)
        assert msg["type"] == "websocket.accept", msg
        return self

    async def __aexit__(self, *exc) -> None:
        with contextlib.suppress(Exception):
            await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
        if self._task is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._task, timeout=2.0)

    # ----- ASGI callables handed to the endpoint -----

    async def _recv(self) -> dict[str, Any]:
        return await self._to_app.get()

    async def _send(self, message: dict[str, Any]) -> None:
        await self._from_app.put(message)

    # ----- test-facing API -----

    async def send_json(self, payload: dict[str, Any]) -> None:
        await self._to_app.put({"type": "websocket.receive", "text": json.dumps(payload)})

    async def recv_json(self, timeout: float = 2.0) -> dict[str, Any]:
        msg = await asyncio.wait_for(self._from_app.get(), timeout=timeout)
        if msg["type"] == "websocket.close":
            self.closed_code = msg.get("code")
            raise WebSocketClosed(self.closed_code)
        assert msg["type"] == "websocket.send", msg
        return json.loads(msg["text"])

    async def expect_closed(self, timeout: float = 2.0) -> int:
        """Wait for a close frame and return its code (fails if a normal frame arrives)."""
        try:
            frame = await self.recv_json(timeout=timeout)
        except WebSocketClosed as exc:
            return exc.code
        raise AssertionError(f"expected close, got frame: {frame}")
