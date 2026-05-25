"""Gmail live emission: orchestrator drain -> OIDC-signed Pub/Sub push -> receiver.

Drives the real delivery path: inject a live email, run one orchestrator drain
pointed at a loopback receiver, and assert the push arrived with an OIDC JWT that
verifies against the customer's public key and an envelope decoding to
{emailAddress, historyId}.
"""
from __future__ import annotations

import base64
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
import pytest

from spammers.director.orchestrator import EmissionLoop
from spammers.orggen.live import inject_gmail_message

pytestmark = pytest.mark.asyncio(loop_scope="session")


@contextmanager
def receiver(path: str):
    got: list[dict] = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            got.append({"headers": dict(self.headers), "body": self.rfile.read(n)})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}{path}", got
    finally:
        srv.shutdown()


async def test_gmail_live_push_delivered(pool, gmail_run):
    vnow = await pool.fetchval("SELECT virtual_now FROM org.runs WHERE id=$1", gmail_run)
    event_id = await inject_gmail_message(pool, gmail_run, text="Live push test", at_virtual=vnow)

    with receiver("/webhooks/gmail/pubsub") as (url, got):
        loop = EmissionLoop(pool, gmail_run, gmail_pubsub_url=url)
        await loop._drain_once()

    assert len(got) == 1
    rec = got[0]
    cust = await pool.fetchrow(
        "SELECT pubsub_oidc_public_key, pubsub_audience FROM app_gmail.customers WHERE run_id=$1", gmail_run)
    tok = rec["headers"].get("Authorization", "").split(" ", 1)[1]
    decoded = jwt.decode(tok, cust["pubsub_oidc_public_key"], algorithms=["RS256"],
                         audience=cust["pubsub_audience"])
    assert decoded["iss"] == "https://accounts.google.com" and decoded["email_verified"] is True
    env = json.loads(rec["body"])
    data = json.loads(base64.b64decode(env["message"]["data"]))
    assert "emailAddress" in data and "historyId" in data
    emitted = await pool.fetchval("SELECT emitted_at FROM timeline.events WHERE id=$1", event_id)
    assert emitted is not None
