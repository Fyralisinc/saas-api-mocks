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
from spammers.notion.webhooks import emit_verification
from spammers.orggen.live import inject_notion_page, inject_notion_page_update

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
        "SELECT verification_token, workspace_id, workspace_name FROM app_notion.integrations WHERE run_id=$1",
        notion_run)
    expect = "sha256=" + hmac.new(integ["verification_token"].encode(), rec["body"], hashlib.sha256).hexdigest()
    assert rec["headers"].get("X-Notion-Signature") == expect
    env = json.loads(rec["body"])
    assert env["type"].startswith("page.") and env["entity"]["type"] == "page"
    assert env["workspace_id"] == integ["workspace_id"]
    # Faithful Notion envelope: enriched top-level fields are present.
    assert env["workspace_name"] == integ["workspace_name"]
    assert env["subscription_id"] and env["integration_id"]
    assert env["authors"] and env["authors"][0]["type"] == "bot"
    assert env["attempt_number"] == 1 and "data" in env
    emitted = await pool.fetchval("SELECT emitted_at FROM timeline.events WHERE id=$1", event_id)
    assert emitted is not None


async def test_notion_live_update_dedups(pool, notion_run):
    """A live edit of an existing page emits a thin content_updated event and
    adds NO new page row (external_id dedups to the backfilled copy)."""
    integ_pk = await pool.fetchval(
        "SELECT id FROM app_notion.integrations WHERE run_id=$1", notion_run)
    before = await pool.fetchval(
        "SELECT count(*) FROM app_notion.pages WHERE integration_pk=$1", integ_pk)
    vnow = await pool.fetchval("SELECT virtual_now FROM org.runs WHERE id=$1", notion_run)
    ev = await inject_notion_page_update(pool, notion_run, at_virtual=vnow)
    after = await pool.fetchval(
        "SELECT count(*) FROM app_notion.pages WHERE integration_pk=$1", integ_pk)
    assert after == before  # update, not create
    raw = await pool.fetchval("SELECT payload FROM timeline.events WHERE id=$1", ev)
    payload = raw if isinstance(raw, dict) else json.loads(raw)
    assert payload["event_type"] == "page.content_updated" and payload["page_id"]


async def test_notion_verification_handshake(pool, notion_run):
    """The one-time subscription handshake: unsigned POST {verification_token}."""
    with receiver("/webhooks/notion") as (url, got):
        status, _ = await emit_verification(pool, run_id=notion_run, notion_webhook_url=url)
    assert status == 200 and len(got) == 1
    integ = await pool.fetchrow(
        "SELECT verification_token FROM app_notion.integrations WHERE run_id=$1", notion_run)
    body = json.loads(got[0]["body"])
    assert body == {"verification_token": integ["verification_token"]}
    # Pre-verify handshake is unsigned — no signature header.
    assert "X-Notion-Signature" not in got[0]["headers"]
