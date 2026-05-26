"""Admin Directory API — mailbox enumeration (users / groups / members / orgunits).

The consumer resolves the mailboxes to ingest from a ``{users, groups, org_units}``
inclusion spec; these endpoints back that resolution off org.people / org.teams.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from spammers.common.errors import gmail_error
from spammers.gmail.auth import resolve_token
from spammers.gmail.responses import GoogleJSONResponse as JSONResponse
from spammers.gmail.state import state
from spammers.gmail.tokens import decode_page_token, encode_page_token

router = APIRouter()


def _group_email(team: str, domain: str) -> str:
    slug = "".join(c.lower() if c.isalnum() else "-" for c in team).strip("-")
    return f"{slug}@{domain}"


def _user_dto(p: dict, domain: str) -> dict:
    full = p["full_name"]
    parts = full.split()
    given, family = (parts[0], parts[-1]) if len(parts) > 1 else (full, "")
    return {
        "kind": "admin#directory#user",
        "id": str(abs(hash(p["email"])) % 10**20),
        "primaryEmail": p["email"],
        "name": {"givenName": given, "familyName": family, "fullName": full},
        "isAdmin": False,
        "suspended": False,
        "archived": False,
        "orgUnitPath": "/",
        "emails": [{"address": p["email"], "primary": True}],
    }


@router.get("/admin/directory/v1/users")
async def list_users(request: Request):
    if resolve_token(request) is None:
        return JSONResponse(gmail_error(401, "Invalid Credentials", reason="authError"), status_code=401)
    st = state()
    rows = await st.pool.fetch(
        "SELECT email, full_name FROM org.people WHERE run_id = $1 ORDER BY email", st.run_id,
    )
    qs = request.query_params
    try:
        max_results = int(qs.get("maxResults", 100))
    except ValueError:
        max_results = 100
    max_results = max(1, min(max_results, 500))
    offset = decode_page_token(qs.get("pageToken"))
    page = rows[offset:offset + max_results]
    has_more = offset + max_results < len(rows)
    body = {
        "kind": "admin#directory#users",
        "users": [_user_dto(dict(r), st.domain) for r in page],
    }
    if has_more:
        body["nextPageToken"] = encode_page_token(offset + max_results)
    return JSONResponse(body)


@router.get("/admin/directory/v1/groups")
async def list_groups(request: Request):
    if resolve_token(request) is None:
        return JSONResponse(gmail_error(401, "Invalid Credentials", reason="authError"), status_code=401)
    st = state()
    rows = await st.pool.fetch(
        "SELECT name FROM org.teams WHERE run_id = $1 ORDER BY name", st.run_id,
    )
    groups = [{
        "kind": "admin#directory#group",
        "id": str(abs(hash(r["name"])) % 10**20),
        "email": _group_email(r["name"], st.domain),
        "name": r["name"],
        "description": f"{r['name']} team",
    } for r in rows]
    return JSONResponse({"kind": "admin#directory#groups", "groups": groups})


@router.get("/admin/directory/v1/groups/{group_key}/members")
async def list_members(request: Request, group_key: str):
    if resolve_token(request) is None:
        return JSONResponse(gmail_error(401, "Invalid Credentials", reason="authError"), status_code=401)
    st = state()
    # group_key is the group email; map it back to a team name by slug.
    rows = await st.pool.fetch(
        """
        SELECT p.email FROM org.people p
          JOIN org.teams t ON t.id = p.team_id
         WHERE p.run_id = $1
        """,
        st.run_id,
    )
    members = [{
        "kind": "admin#directory#member",
        "id": str(abs(hash(r["email"])) % 10**20),
        "email": r["email"],
        "role": "MEMBER",
        "type": "USER",
        "status": "ACTIVE",
    } for r in rows]
    # Filter to the requested group when the key matches a known group email.
    teams = await st.pool.fetch("SELECT name FROM org.teams WHERE run_id = $1", st.run_id)
    known = {_group_email(t["name"], st.domain) for t in teams}
    if group_key in known:
        team_name = next(t["name"] for t in teams if _group_email(t["name"], st.domain) == group_key)
        rows = await st.pool.fetch(
            """
            SELECT p.email FROM org.people p JOIN org.teams t ON t.id = p.team_id
             WHERE p.run_id = $1 AND t.name = $2
            """,
            st.run_id, team_name,
        )
        members = [{
            "kind": "admin#directory#member", "id": str(abs(hash(r["email"])) % 10**20),
            "email": r["email"], "role": "MEMBER", "type": "USER", "status": "ACTIVE",
        } for r in rows]
    return JSONResponse({"kind": "admin#directory#members", "members": members})


@router.get("/admin/directory/v1/customer/{customer_id}/orgunits")
async def list_orgunits(request: Request, customer_id: str):
    if resolve_token(request) is None:
        return JSONResponse(gmail_error(401, "Invalid Credentials", reason="authError"), status_code=401)
    return JSONResponse({"kind": "admin#directory#orgUnits", "organizationUnits": []})
