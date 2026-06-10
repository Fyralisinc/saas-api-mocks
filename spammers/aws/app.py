"""AWS mock — FastAPI app speaking the GENUINE AWS wire protocols.

botocore (pointed here via an ``endpoint_url`` override — the moto/localstack
seam) hits a single ``POST /`` and the mock dispatches on the request shape:

  * ``X-Amz-Target: …CloudTrail_20131101.LookupEvents`` → CloudTrail JSON 1.1.
        body  {StartTime,EndTime,MaxResults,NextToken,LookupAttributes,EventCategory}
        reply {"Events":[<Event>…], "NextToken":"…"}  (newest-first, ≤50/page)
  * form ``Action=GetCallerIdentity|AssumeRole`` → STS Query (XML reply).

Every request is **SigV4-signed**; the mock parses ``Authorization``, resolves the
secret-access-key (the seeded install key, or an ASIA… temp key minted by
AssumeRole), recomputes the signature, and rejects a mismatch with HTTP 403
(``SignatureDoesNotMatch`` / ``InvalidClientTokenId``) — the faithful failure a
tampered request hits against real AWS.

Mock-only: ``POST /_control/rate_limit?count=N`` arms N forced throttles
(``ThrottlingException``) on the next CloudTrail calls, to drive a consumer's
retry budget. CloudTrail's documented limit is 2 req/s/account/Region.
"""
from __future__ import annotations

import base64
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import parse_qsl, quote
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from spammers.aws import dto as _dto
from spammers.aws import sigv4 as _sigv4
from spammers.aws import state as _state

_CT_TARGET_OP = "LookupEvents"
_FORCED_THROTTLE = {"count": 0}


@asynccontextmanager
async def _lifespan(app: FastAPI):
    await _state.startup()
    yield
    await _state.shutdown()


# --------------------------------------------------------------------------- errors


def _json_error(status: int, code: str, message: str) -> JSONResponse:
    # JSON 1.1 protocol error. botocore keys the exception on `__type`.
    resp = JSONResponse(_dto.cloudtrail_error(code, message), status_code=status)
    resp.headers["x-amzn-ErrorType"] = code
    return resp


def _xml(body: str, status: int = 200) -> Response:
    return Response(content=body, status_code=status,
                    media_type="text/xml")


def _sts_error(status: int, code: str, message: str, fault: str = "Sender") -> Response:
    return _xml(_dto.sts_error_xml(code=code, message=message,
                                   request_id=_request_id(), fault=fault),
                status=status)


def _request_id() -> str:
    return str(uuid4())


# --------------------------------------------------------------------------- SigV4


def _canonical_query(query_string: str) -> str:
    if not query_string:
        return ""
    pairs = parse_qsl(query_string, keep_blank_values=True)
    enc = sorted((quote(k, safe="-_.~"), quote(v, safe="-_.~")) for k, v in pairs)
    return "&".join(f"{k}={v}" for k, v in enc)


async def _resolve_secret(pool, run_id, access_key_id: str) -> Optional[str]:
    temp = _sigv4.temp_credential(access_key_id)
    if temp is not None:
        return temp.secret_access_key
    return await _state.secret_for_access_key(pool, run_id, access_key_id)


async def _verify(request: Request, raw_body: bytes) -> Optional[str]:
    """Return None when SigV4 verifies, else an error code (the caller maps it to
    a protocol-appropriate 403)."""
    headers = {k.lower(): v for k, v in request.headers.items()}
    auth = _sigv4.parse_authorization(headers.get("authorization"),
                                      headers.get("x-amz-date"))
    if auth is None:
        return "MissingAuthenticationToken"
    s = _state.state()
    secret = await _resolve_secret(s.pool, s.run_id, auth.access_key_id)
    if not secret:
        return "InvalidClientTokenId"
    ok = _sigv4.verify(
        auth, method=request.method, path=request.url.path,
        query_string=_canonical_query(request.url.query),
        headers=headers, body=raw_body, secret_access_key=secret,
    )
    return None if ok else "SignatureDoesNotMatch"


_SIG_MESSAGES = {
    "SignatureDoesNotMatch": ("The request signature we calculated does not match "
                              "the signature you provided. Check your AWS Secret "
                              "Access Key and signing method."),
    "InvalidClientTokenId": "The security token included in the request is invalid.",
    "MissingAuthenticationToken": ("Request is missing Authentication Token."),
}


# --------------------------------------------------------------------------- tokens


def _encode_token(offset: int, start_ms: int | None, end_ms: int | None) -> str:
    raw = json.dumps({"o": offset, "s": start_ms, "e": end_ms}).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_token(token: str) -> Optional[dict]:
    try:
        return json.loads(base64.urlsafe_b64decode(token.encode()).decode())
    except Exception:  # noqa: BLE001 — malformed token
        return None


# --------------------------------------------------------------------------- app


def create_app() -> FastAPI:
    app = FastAPI(title="AWS mock", lifespan=_lifespan)

    @app.get("/_health")
    async def health():
        s = _state.state()
        inst = await _state.install_for_run(s.pool, s.run_id)
        return {"ok": True, "service": "aws-mock", "run_id": str(s.run_id),
                "account_id": inst["account_id"] if inst else None,
                "region": inst["region"] if inst else None}

    @app.post("/_control/rate_limit")
    async def arm_rate_limit(count: int = 1):
        _FORCED_THROTTLE["count"] = max(0, count)
        return {"armed": _FORCED_THROTTLE["count"]}

    @app.post("/")
    async def aws_endpoint(request: Request):
        raw = await request.body()
        target = request.headers.get("x-amz-target") or request.headers.get("X-Amz-Target")
        if target:
            return await _cloudtrail(request, raw, target)
        # STS Query protocol — Action in the form body.
        form = dict(parse_qsl(raw.decode("utf-8", "replace"), keep_blank_values=True))
        action = form.get("Action")
        if action:
            return await _sts(request, raw, action, form)
        return _json_error(400, "UnknownOperationException",
                           "No X-Amz-Target or Action could be resolved.")

    # ---- CloudTrail (JSON 1.1) -------------------------------------------

    async def _cloudtrail(request: Request, raw: bytes, target: str) -> Response:
        op = target.rsplit(".", 1)[-1]
        if op != _CT_TARGET_OP:
            return _json_error(400, "UnknownOperationException",
                               f"Operation {op!r} is not supported by this mock.")
        err = await _verify(request, raw)
        if err is not None:
            return _json_error(403, err, _SIG_MESSAGES.get(err, err))
        if _FORCED_THROTTLE["count"] > 0:
            _FORCED_THROTTLE["count"] -= 1
            return _json_error(400, "ThrottlingException", "Rate exceeded")

        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return _json_error(400, "SerializationException", "Could not parse request body")
        if not isinstance(body, dict):
            return _json_error(400, "SerializationException", "Request body is not an object")

        s = _state.state()
        inst = await _state.install_for_run(s.pool, s.run_id)
        if inst is None:
            return JSONResponse({"Events": []})

        start_ms = _ms_from_epoch_seconds(body.get("StartTime"))
        end_ms = _ms_from_epoch_seconds(body.get("EndTime"))
        max_results = body.get("MaxResults")
        try:
            max_results = int(max_results) if max_results is not None else 50
        except (TypeError, ValueError):
            return _json_error(400, "InvalidMaxResultsException",
                               "MaxResults must be an integer")
        if max_results < 1 or max_results > 50:
            return _json_error(400, "InvalidMaxResultsException",
                               "MaxResults must be between 1 and 50")

        attrs = body.get("LookupAttributes") or []
        if isinstance(attrs, list) and len(attrs) > 1:
            return _json_error(400, "InvalidLookupAttributesException",
                               "Only one lookup attribute is currently supported.")
        attr_key = attr_val = None
        if isinstance(attrs, list) and attrs and isinstance(attrs[0], dict):
            attr_key = attrs[0].get("AttributeKey")
            attr_val = attrs[0].get("AttributeValue")

        offset = 0
        token = body.get("NextToken")
        if token:
            decoded = _decode_token(token)
            if decoded is None or decoded.get("s") != start_ms or decoded.get("e") != end_ms:
                return _json_error(400, "InvalidNextTokenException",
                                   "Invalid NextToken (must be re-sent with identical "
                                   "StartTime/EndTime).")
            offset = int(decoded.get("o") or 0)

        rows = await _query_events(s.pool, inst["id"], start_ms, end_ms,
                                   attr_key, attr_val)
        page = rows[offset:offset + max_results]
        next_off = offset + max_results
        next_token = (_encode_token(next_off, start_ms, end_ms)
                      if next_off < len(rows) else None)
        return JSONResponse(_dto.lookup_events_response(
            [dict(r) for r in page], next_token))

    # ---- STS (Query) -----------------------------------------------------

    async def _sts(request: Request, raw: bytes, action: str, form: dict) -> Response:
        err = await _verify(request, raw)
        if err is not None:
            status = 403
            return _sts_error(status, err, _SIG_MESSAGES.get(err, err))
        s = _state.state()
        inst = await _state.install_for_run(s.pool, s.run_id)
        if inst is None:
            return _sts_error(403, "InvalidClientTokenId",
                              "No install provisioned for this run.")
        if action == "GetCallerIdentity":
            return _xml(_dto.get_caller_identity_xml(
                arn=inst["iam_user_arn"], user_id=inst["user_id"],
                account=inst["account_id"], request_id=_request_id()))
        if action == "AssumeRole":
            return _assume_role(inst, form)
        return _sts_error(400, "InvalidAction",
                          f"Could not find operation {action} for version 2011-06-15")

    def _assume_role(inst, form: dict) -> Response:
        role_arn = form.get("RoleArn") or inst["role_arn"]
        session_name = form.get("RoleSessionName") or "fyralis-ingest"
        try:
            duration = int(form.get("DurationSeconds") or 3600)
        except (TypeError, ValueError):
            duration = 3600
        if duration < 900 or duration > 43200:
            return _sts_error(400, "ValidationError",
                              "DurationSeconds must be between 900 and 43200.")
        # Deterministic-enough mint (no Date.now in mock corpus, but live mint here
        # is process-local and short-lived; the slice uses it immediately).
        suffix = uuid4().hex[:16].upper()
        temp_ak = "ASIA" + suffix
        temp_secret = "mockSecret/" + uuid4().hex
        session_token = base64.b64encode(
            json.dumps({"ak": temp_ak, "role": role_arn}).encode()).decode()
        role_name = role_arn.rsplit("/", 1)[-1]
        assumed_arn = (f"arn:aws:sts::{inst['account_id']}:assumed-role/"
                       f"{role_name}/{session_name}")
        assumed_id = f"AROAEXAMPLEMOCKID:{session_name}"
        expiration = datetime.now(timezone.utc) + timedelta(seconds=duration)
        _sigv4.remember_temp_credential(_sigv4.TempCredential(
            access_key_id=temp_ak, secret_access_key=temp_secret,
            session_token=session_token,
            expiration_ms=int(expiration.timestamp() * 1000),
            account_id=inst["account_id"], assumed_role_arn=assumed_arn,
            assumed_role_id=assumed_id))
        return _xml(_dto.assume_role_xml(
            access_key_id=temp_ak, secret_access_key=temp_secret,
            session_token=session_token,
            expiration_iso=expiration.strftime("%Y-%m-%dT%H:%M:%SZ"),
            assumed_role_id=assumed_id, assumed_role_arn=assumed_arn,
            packed_policy_size=6, request_id=_request_id()))

    return app


def _ms_from_epoch_seconds(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value * 1000)
    return None


async def _query_events(pool, install_pk, start_ms: int | None, end_ms: int | None,
                        attr_key: str | None, attr_val: str | None) -> list:
    clauses = ["install_pk = $1"]
    params: list[Any] = [install_pk]
    if start_ms is not None:
        params.append(start_ms); clauses.append(f"event_time_ms >= ${len(params)}")
    if end_ms is not None:
        params.append(end_ms); clauses.append(f"event_time_ms <= ${len(params)}")
    # Single LookupAttribute (the common keys). Real CloudTrail allows exactly one.
    _ATTR_COL = {"EventName": "event_name", "EventSource": "event_source",
                 "Username": "username", "EventId": "event_id",
                 "AccessKeyId": "access_key_id"}
    if attr_key in _ATTR_COL and attr_val is not None:
        params.append(attr_val); clauses.append(f"{_ATTR_COL[attr_key]} = ${len(params)}")
    elif attr_key == "ReadOnly" and attr_val is not None:
        params.append(str(attr_val).lower() == "true")
        clauses.append(f"read_only = ${len(params)}")
    sql = (
        "SELECT event_id, event_time_ms, event_name, event_source, aws_region, "
        "username, access_key_id, read_only, resources, record "
        "FROM app_aws.events WHERE " + " AND ".join(clauses) +
        # Newest-first — LookupEvents returns the most recent event first.
        " ORDER BY event_time_ms DESC, event_id DESC"
    )
    return await pool.fetch(sql, *params)


app = create_app()
