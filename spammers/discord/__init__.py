"""Discord mock (:7002 HTTP + WS).

Surfaces (docs/ARCHITECTURE.md §5.2):

- **Install**: ``GET /oauth2/authorize``, ``POST /api/v10/oauth2/token``.
- **REST** (``Authorization: Bot <token>``): ``users/@me``, ``users/{id}``,
  ``guilds/{id}`` (+ ``/channels``, ``/members/{user}``), ``channels/{id}``
  (+ ``/messages`` read/write), application command registration.
- **Gateway (WebSocket)**: full opcode handshake — HELLO(10), IDENTIFY(2),
  HEARTBEAT(1)/ACK(11), dispatch(0) of ``READY`` / ``GUILD_CREATE`` /
  ``MESSAGE_CREATE``; RESUME(6) from a per-session ring buffer;
  RECONNECT(7) / INVALID_SESSION(9). Live messages are pushed to connected
  bots by an in-process dispatcher (no historical replay on connect).
- **Interactions webhook**: Ed25519-signed POSTs to the consumer (ping /
  command / component), emitted by the Director.
- **Rate limits**: per-route token buckets with ``X-RateLimit-*`` headers;
  ``429 {"message":"You are being rate limited.","retry_after":…,"global":…}``.

All responses use ``Content-Type: application/json; charset=utf-8`` and the
real Discord object shapes.
"""
