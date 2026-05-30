"""Fixtures for the Google Drive mock fidelity suite (Drive API v3).

Reuses the session ``pool``; seeds a deterministic installation, one My Drive
(alice) + one Shared Drive, a known set of files (Google-native doc with
exported text, a plain-text file, a trashed file, and two on the Shared Drive)
with staggered ``modified_time`` + monotonic ``change_seq``, plus comments and
revisions. Wires the Drive ``state`` singleton + an ASGI client; mints DWD
bearers directly.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from spammers.common.google_token import mint_access_token
from spammers.common.signing import generate_rsa_keypair

DOMAIN = "drive-test.com"
ALICE = f"alice@{DOMAIN}"
BOB = f"bob@{DOMAIN}"
SCOPE = "https://www.googleapis.com/auth/drive.readonly"
SHARED_DRIVE_ID = "0ABCdef0000000000001"
_T0 = datetime(2026, 1, 10, 9, 0, tzinfo=timezone.utc)

# (file_id, name, mime, version, trashed, extracted_text, modified_offset_h, drive)
FILES = [
    ("1fileMyDoc00000000000000000001", "Design doc", "application/vnd.google-apps.document",
     3, False, "This is the exported design document body.", 0, "my"),
    ("1fileMyTxt00000000000000000002", "notes.txt", "text/plain",
     1, False, "Plain text notes content.", 2, "my"),
    ("1fileMyOld00000000000000000003", "Old draft", "application/vnd.google-apps.document",
     5, True, "Trashed draft body.", 4, "my"),
    ("1fileSh0001000000000000000004", "Team charter", "application/vnd.google-apps.document",
     2, False, "Shared drive charter text.", 1, "shared"),
    ("1fileSh0002000000000000000005", "roadmap.md", "text/markdown",
     4, False, "# Roadmap\n- item", 3, "shared"),
]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def drive_run(pool) -> UUID:
    run_id = uuid4()
    await pool.execute(
        """INSERT INTO org.runs (id, size, runtime, seed, fyralis_tenant_id,
               fyralis_base_url, virtual_now, mode, speed_multiplier)
           VALUES ($1,'small','few_months',6,$2,'http://localhost:8000',now(),'frozen',1.0)""",
        run_id, uuid4())
    people = {}
    for handle, email in (("alice", ALICE), ("bob", BOB)):
        pid = uuid4(); people[email] = pid
        await pool.execute(
            """INSERT INTO org.people (id, run_id, handle, full_name, email, role, level, timezone, started_at)
               VALUES ($1,$2,$3,$4,$5,'engineer','mid','UTC',now())""",
            pid, run_id, handle, handle.title(), email)
    priv, pub = generate_rsa_keypair()
    inst_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_drive.installations
            (id, run_id, customer_id, domain, service_account_email,
             service_account_client_id, service_account_private_key, service_account_public_key)
           VALUES ($1,$2,'C9',$3,'sa@drive.iam.gserviceaccount.com','cid',$4,$5)""",
        inst_pk, run_id, DOMAIN, priv, pub)

    my_pk = uuid4(); shared_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_drive.drives (id, installation_pk, drive_id, name, kind, owner_person_id, owner_email, created_at)
           VALUES ($1,$2,'md-alice','Alice — My Drive','my_drive',$3,$4,$5)""",
        my_pk, inst_pk, people[ALICE], ALICE, _T0)
    await pool.execute(
        """INSERT INTO app_drive.drives (id, installation_pk, drive_id, name, kind, owner_person_id, owner_email, created_at)
           VALUES ($1,$2,$3,'Engineering Drive','shared_drive',NULL,$4,$5)""",
        shared_pk, inst_pk, SHARED_DRIVE_ID, ALICE, _T0)

    # change_seq assigned in modified order across the install.
    ordered = sorted(FILES, key=lambda f: f[6])
    seq = 0
    first_file_pk = None
    for fid, name, mime, ver, trashed, text, moff, drv in ordered:
        seq += 1
        drive_pk = my_pk if drv == "my" else shared_pk
        modified = _T0 + timedelta(hours=moff)
        size = None if mime.startswith("application/vnd.google-apps") else len(text)
        fpk = uuid4()
        if first_file_pk is None and drv == "my" and not trashed:
            first_file_pk = fpk
        await pool.execute(
            """INSERT INTO app_drive.files
                (id, installation_pk, drive_pk, file_id, name, mime_type, version, trashed,
                 explicitly_trashed, size, web_view_link, owner_email, owner_name,
                 last_modifying_email, last_modifying_name, parents, shared, starred,
                 extracted_text, created_time, modified_time, change_seq)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,FALSE,$9,$10,$11,'Alice',$11,'Alice',
                       '[]'::jsonb,$12,FALSE,$13,$14,$15,$16)""",
            fpk, inst_pk, drive_pk, fid, name, mime, ver, trashed, size,
            f"https://drive.google.com/file/d/{fid}/view", ALICE,
            drv == "shared", text, _T0, modified, seq)
        if fpk == first_file_pk:
            await pool.execute(
                """INSERT INTO app_drive.comments
                    (id, file_pk, comment_id, content, author_name, author_email, resolved,
                     quoted_value, replies, created_time, modified_time, position)
                   VALUES ($1,$2,'cmt001','Looks good to me.','Bob',$3,FALSE,NULL,'[]'::jsonb,$4,$4,0)""",
                uuid4(), fpk, BOB, modified)
            for ri in range(2):
                await pool.execute(
                    """INSERT INTO app_drive.revisions
                        (id, file_pk, revision_id, keep_forever, published, size,
                         last_modifying_email, last_modifying_name, modified_time, position)
                       VALUES ($1,$2,$3,FALSE,FALSE,NULL,$4,'Alice',$5,$6)""",
                    uuid4(), fpk, f"rev{ri}", ALICE, modified - timedelta(hours=1 - ri), ri)
    return run_id


@pytest_asyncio.fixture(loop_scope="session")
async def drive_client(pool, drive_run):
    from spammers.drive import state as d_state
    from spammers.drive.app import create_app
    from spammers.drive.ratelimit import _RL

    d_state._STATE = d_state.DriveMockState(pool=pool, run_id=drive_run)
    _RL._buckets.clear()
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://mock") as c:
        yield c
    d_state._STATE = None


def drive_token(sub: str = ALICE) -> str:
    tok, _ = mint_access_token(sub, SCOPE)
    return tok


@pytest.fixture
def drive_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {drive_token()}"}
