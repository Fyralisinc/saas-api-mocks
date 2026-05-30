"""Google Drive mock — contract + behavior fidelity (Drive API v3).

Encodes the real Drive v3 behavior the consumer's backfill + changes poller rely
on: DWD token exchange, files.list (My Drive corpora=user vs Shared Drive
corpora=drive), the modifiedTime window, the changes feed (startPageToken ->
empty delta + newStartPageToken on a frozen run; full replay from the start),
export/comments/revisions, and per-user 429s.
"""
from __future__ import annotations

import urllib.parse

import jwt
import pytest

from spammers.tests.drive.conftest import (
    ALICE,
    SCOPE,
    SHARED_DRIVE_ID,
    drive_token,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ---- auth -----------------------------------------------------------------

async def test_token_exchange(drive_client):
    assertion = jwt.encode({"iss": "sa", "sub": ALICE, "scope": SCOPE}, "k" * 32, algorithm="HS256")
    r = await drive_client.post("/token", data={
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
    assert r.status_code == 200 and r.json()["access_token"].startswith("ya29.")


async def test_unauthed_401(drive_client):
    r = await drive_client.get("/drive/v3/files?corpora=user")
    assert r.status_code == 401 and r.json()["error"]["status"] == "UNAUTHENTICATED"


# ---- files.list -----------------------------------------------------------

async def test_files_list_my_drive(drive_client, drive_auth):
    r = await drive_client.get(
        "/drive/v3/files?corpora=user&q=trashed%20%3D%20false", headers=drive_auth)
    body = r.json()
    assert body["kind"] == "drive#fileList"
    # alice's My Drive has 2 non-trashed files (the trashed one is excluded by q).
    names = {f["name"] for f in body["files"]}
    assert names == {"Design doc", "notes.txt"}
    f = body["files"][0]
    assert isinstance(f["version"], str)          # version is a string in v3
    assert "driveId" not in f                       # My Drive files carry no driveId
    assert f["owners"][0]["emailAddress"] == ALICE


async def test_files_list_shared_drive(drive_client, drive_auth):
    r = await drive_client.get(
        f"/drive/v3/files?corpora=drive&driveId={SHARED_DRIVE_ID}"
        "&includeItemsFromAllDrives=true&supportsAllDrives=true&q=trashed%20%3D%20false",
        headers=drive_auth)
    body = r.json()
    assert len(body["files"]) == 2
    assert all(f["driveId"] == SHARED_DRIVE_ID for f in body["files"])


async def test_files_modified_after(drive_client, drive_auth):
    # modifiedTime > _T0+1h on My Drive -> only notes.txt (offset 2h); Design doc is at 0h.
    q = "trashed = false and modifiedTime > '2026-01-10T10:00:00.000Z'"
    r = await drive_client.get(
        f"/drive/v3/files?corpora=user&q={urllib.parse.quote(q)}", headers=drive_auth)
    names = {f["name"] for f in r.json()["files"]}
    assert names == {"notes.txt"}


async def test_files_pagination(drive_client, drive_auth):
    r = await drive_client.get(
        f"/drive/v3/files?corpora=drive&driveId={SHARED_DRIVE_ID}&pageSize=1", headers=drive_auth)
    body = r.json()
    assert len(body["files"]) == 1 and "nextPageToken" in body
    r2 = await drive_client.get(
        f"/drive/v3/files?corpora=drive&driveId={SHARED_DRIVE_ID}&pageSize=1"
        f"&pageToken={urllib.parse.quote(body['nextPageToken'])}", headers=drive_auth)
    assert len(r2.json()["files"]) == 1 and "nextPageToken" not in r2.json()


# ---- changes feed ---------------------------------------------------------

async def test_start_page_token_then_empty_delta(drive_client, drive_auth):
    spt = (await drive_client.get(
        "/drive/v3/changes/startPageToken?supportsAllDrives=true", headers=drive_auth)
        ).json()["startPageToken"]
    body = (await drive_client.get(
        f"/drive/v3/changes?pageToken={urllib.parse.quote(spt)}&includeRemoved=true"
        "&supportsAllDrives=true&includeItemsFromAllDrives=true", headers=drive_auth)).json()
    # Frozen run: nothing changed since backfill -> empty + a fresh start token.
    assert body["changes"] == []
    assert "newStartPageToken" in body and "nextPageToken" not in body


async def test_changes_full_replay(drive_client, drive_auth):
    from spammers.drive.tokens import encode_change_token
    body = (await drive_client.get(
        f"/drive/v3/changes?pageToken={urllib.parse.quote(encode_change_token(1))}"
        "&includeRemoved=true&supportsAllDrives=true&includeItemsFromAllDrives=true",
        headers=drive_auth)).json()
    # All 5 seeded files surface as changes, in change_seq order.
    assert len(body["changes"]) == 5
    assert "newStartPageToken" in body
    ch = body["changes"][0]
    assert ch["kind"] == "drive#change" and ch["changeType"] == "file"
    assert ch["removed"] is False and "file" in ch


# ---- content + sub-resources ----------------------------------------------

async def test_export_and_alt_media(drive_client, drive_auth):
    files = (await drive_client.get("/drive/v3/files?corpora=user&q=trashed%20%3D%20false",
                                    headers=drive_auth)).json()["files"]
    doc = next(f for f in files if f["mimeType"].endswith("document"))
    r = await drive_client.get(f"/drive/v3/files/{doc['id']}/export?mimeType=text/plain", headers=drive_auth)
    assert r.status_code == 200 and "design document" in r.text

    txt = next(f for f in files if f["mimeType"] == "text/plain")
    r = await drive_client.get(f"/drive/v3/files/{txt['id']}?alt=media", headers=drive_auth)
    assert r.status_code == 200 and "Plain text notes" in r.text


async def test_comments_and_revisions(drive_client, drive_auth):
    # The earliest My Drive file (Design doc, modified offset 0h) carries the seeded comment + revisions.
    doc_id = "1fileMyDoc00000000000000000001"
    cr = await drive_client.get(f"/drive/v3/files/{doc_id}/comments", headers=drive_auth)
    cbody = cr.json()
    assert cbody["kind"] == "drive#commentList" and len(cbody["comments"]) == 1
    assert cbody["comments"][0]["content"] == "Looks good to me."

    rr = await drive_client.get(f"/drive/v3/files/{doc_id}/revisions", headers=drive_auth)
    rbody = rr.json()
    assert rbody["kind"] == "drive#revisionList" and len(rbody["revisions"]) == 2


async def test_file_404(drive_client, drive_auth):
    r = await drive_client.get("/drive/v3/files/nope/export?mimeType=text/plain", headers=drive_auth)
    assert r.status_code == 404 and r.json()["error"]["status"] == "NOT_FOUND"


async def test_drives_enumeration(drive_client, drive_auth):
    r = await drive_client.get("/drive/v3/drives?useDomainAdminAccess=true", headers=drive_auth)
    body = r.json()
    assert body["kind"] == "drive#driveList"
    assert [d["id"] for d in body["drives"]] == [SHARED_DRIVE_ID]


# ---- rate limiting --------------------------------------------------------

async def test_rate_limit_429(drive_client, drive_auth):
    from spammers.drive.ratelimit import _RL, _CAP, _REFILL
    await _RL.take(f"drive:{ALICE}", capacity=_CAP, refill_per_sec=_REFILL, cost=_CAP)
    r = await drive_client.get("/drive/v3/files?corpora=user", headers=drive_auth)
    assert r.status_code == 429
    assert r.json()["error"]["errors"][0]["reason"] == "rateLimitExceeded"
    assert int(r.headers["Retry-After"]) >= 1
