"""Notion live emission: orchestrator drain -> signed thin webhook -> receiver.

Drives the real delivery path (not just the signer): inject a live page, run one
orchestrator drain pointed at a loopback HTTP receiver, and assert the POST
arrived with a valid X-Notion-Signature and the event was marked emitted.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from spammers.director.orchestrator import EmissionLoop
from spammers.orggen.live import inject_notion_page

pytestmark = pytest.mark.asyncio(loop_scope="session")


@contextmanager
def receiver(path: str):
    got: list[dict] = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence
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


async def test_notion_live_webhook_delivered(pool, notion_run):
    vnow = await pool.fetchval("SELECT virtual_now FROM org.runs WHERE id=$1", notion_run)
    event_id = await inject_notion_page(pool, notion_run, title="Live emit page", at_virtual=vnow)

    with receiver("/webhooks/notion") as (url, got):
        loop = EmissionLoop(pool, notion_run, notion_webhook_url=url)
        await loop._drain_once()

    assert len(got) == 1
    rec = got[0]
    integ = await pool.fetchrow(
        "SELECT verification_token, workspace_id FROM app_notion.integrations WHERE run_id=$1", notion_run)
    expect = "sha256=" + hmac.new(integ["verification_token"].encode(), rec["body"], hashlib.sha256).hexdigest()
    assert rec["headers"].get("X-Notion-Signature") == expect
    env = json.loads(rec["body"])
    assert env["type"].startswith("page.") and env["entity"]["type"] == "page"
    assert env["workspace_id"] == integ["workspace_id"]
    emitted = await pool.fetchval("SELECT emitted_at FROM timeline.events WHERE id=$1", event_id)
    assert emitted is not None
