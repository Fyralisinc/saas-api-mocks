"""Fixtures for the Gmail mock fidelity suite.

Reuses the session ``pool``; seeds a deterministic customer (with a real RSA
OIDC keypair so push JWTs verify), two mailboxes (alice/bob), and a small
thread on alice's mailbox with two messages + history rows. Wires the Gmail
``state`` singleton + an ASGI client and mints DWD bearers directly.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from spammers.common.google_token import mint_access_token
from spammers.common.signing import generate_rsa_keypair

DOMAIN = "gmail-test.com"
ALICE = f"alice@{DOMAIN}"
BOB = f"bob@{DOMAIN}"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AUDIENCE = "https://ingest.example.com/webhooks/gmail/pubsub"
PUSH_SA = "push@gmail-test-ingest.iam.gserviceaccount.com"
_T = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def gmail_run(pool) -> UUID:
    run_id = uuid4()
    await pool.execute(
        """INSERT INTO org.runs (id, size, runtime, seed, fyralis_tenant_id,
               fyralis_base_url, virtual_now, mode, speed_multiplier)
           VALUES ($1,'small','few_months',5,$2,'http://localhost:8000',now(),'frozen',1.0)""",
        run_id, uuid4())
    people = {}
    for handle, email in (("alice", ALICE), ("bob", BOB)):
        pid = uuid4(); people[email] = pid
        await pool.execute(
            """INSERT INTO org.people (id, run_id, handle, full_name, email, role, level, timezone, started_at)
               VALUES ($1,$2,$3,$4,$5,'engineer','mid','UTC',now())""",
            pid, run_id, handle, handle.title(), email)
    sa_priv, sa_pub = generate_rsa_keypair()
    oidc_priv, oidc_pub = generate_rsa_keypair()
    cust_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_gmail.customers
            (id, run_id, customer_id, domain, organization_name, service_account_email,
             service_account_public_key, pubsub_oidc_public_key, pubsub_oidc_private_key, pubsub_audience)
           VALUES ($1,$2,'C9',$3,'GmailTest',$4,$5,$6,$7,$8)""",
        cust_pk, run_id, DOMAIN, PUSH_SA, sa_pub, oidc_pub, oidc_priv, AUDIENCE)
    mboxes = {}
    for email in (ALICE, BOB):
        mpk = uuid4(); mboxes[email] = mpk
        await pool.execute(
            """INSERT INTO app_gmail.mailboxes (id, customer_pk, person_id, email, history_id, profile)
               VALUES ($1,$2,$3,$4,2,$5::jsonb)""",
            mpk, cust_pk, people[email], email,
            json.dumps({"emailAddress": email, "messagesTotal": 2, "threadsTotal": 1, "historyId": "2"}))
    # alice's mailbox: one thread, two messages (history ids 1 and 2)
    tpk = uuid4()
    await pool.execute(
        "INSERT INTO app_gmail.threads (id, mailbox_pk, thread_id, subject, snippet) VALUES ($1,$2,'t100','Sync',$3)",
        tpk, mboxes[ALICE], "Let's sync")
    for hid, (mid, frm, snippet, body) in enumerate([
        ("m100", BOB, "Can we sync?", "Can we sync this week?"),
        ("m101", ALICE, "Sounds good", "Sounds good, Thursday works."),
    ], start=1):
        headers = [
            {"name": "From", "value": frm}, {"name": "To", "value": ALICE if frm == BOB else BOB},
            {"name": "Subject", "value": "Sync"}, {"name": "Date", "value": "Mon, 05 Jan 2026 12:00:00 +0000"},
            {"name": "Message-ID", "value": f"<{mid}@{DOMAIN}>"}]
        await pool.execute(
            """INSERT INTO app_gmail.messages
                (id, thread_pk, message_id, history_id, rfc822_msg_id, label_ids, headers,
                 snippet, body_plain, body_html, internal_date, size_estimate)
               VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8,$9,'',$10,$11)""",
            uuid4(), tpk, mid, hid, f"<{mid}@{DOMAIN}>",
            json.dumps(["INBOX"] if frm == BOB else ["SENT"]), json.dumps(headers),
            snippet, body, _T, len(body) + 100)
        await pool.execute(
            """INSERT INTO app_gmail.history
                (mailbox_pk, history_id, history_type, message_id, thread_id, label_ids, occurred_at)
               VALUES ($1,$2,'messageAdded',$3,'t100',$4::jsonb,$5)""",
            mboxes[ALICE], hid, mid, json.dumps(["INBOX"]), _T)
    return run_id


@pytest_asyncio.fixture(loop_scope="session")
async def gmail_client(pool, gmail_run):
    from spammers.gmail import state as g_state
    from spammers.gmail.app import create_app
    from spammers.gmail.ratelimit import _RL

    cust = await pool.fetchrow("SELECT * FROM app_gmail.customers WHERE run_id=$1", gmail_run)
    g_state._STATE = g_state.GmailMockState(
        pool=pool, run_id=gmail_run, customer_pk=cust["id"], customer_id=cust["customer_id"],
        domain=cust["domain"], organization_name=cust["organization_name"],
        service_account_email=cust["service_account_email"],
        oidc_private_key=cust["pubsub_oidc_private_key"], oidc_public_key=cust["pubsub_oidc_public_key"],
        pubsub_audience=cust["pubsub_audience"])
    _RL._buckets.clear()
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://mock") as c:
        yield c
    g_state._STATE = None


def gmail_token(sub: str = ALICE) -> str:
    tok, _ = mint_access_token(sub, SCOPE)
    return tok


@pytest.fixture
def gmail_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {gmail_token()}"}
