"""Gmail API response shapes."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any


def _jsonb(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


def _internal_date_ms(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return str(int(dt.timestamp() * 1000))


def _b64url(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii")


def message_ref(row: dict) -> dict:
    """The {id, threadId} stub returned by messages.list / history."""
    return {"id": row["message_id"], "threadId": _thread_id(row)}


def _thread_id(row: dict) -> str:
    # The list/get rows carry the gmail thread_id via a joined column.
    return row.get("gmail_thread_id") or row.get("thread_id")


def message_dto(row: dict, fmt: str = "full") -> dict:
    labels = _jsonb(row.get("label_ids") or [])
    headers = _jsonb(row.get("headers") or [])
    base = {
        "id": row["message_id"],
        "threadId": _thread_id(row),
        "labelIds": labels,
        "snippet": row.get("snippet") or "",
        "historyId": str(row["history_id"]),
        "internalDate": _internal_date_ms(row["internal_date"]),
        "sizeEstimate": row.get("size_estimate") or 0,
    }
    if fmt == "minimal":
        return base
    if fmt == "raw":
        # A minimal RFC822 rendering is enough for the consumer's raw path.
        raw_lines = [f"{h['name']}: {h['value']}" for h in headers]
        raw = "\r\n".join(raw_lines) + "\r\n\r\n" + (row.get("body_plain") or "")
        base["raw"] = base64.urlsafe_b64encode(raw.encode()).decode()
        return base

    body_plain = row.get("body_plain") or ""
    payload: dict = {
        "partId": "",
        "mimeType": "text/plain",
        "filename": "",
        "headers": headers,
    }
    if fmt == "full":
        payload["body"] = {"size": len(body_plain), "data": _b64url(body_plain)}
    else:  # metadata
        payload["body"] = {"size": len(body_plain)}
    base["payload"] = payload
    return base


def thread_dto(thread_id: str, history_id: int, messages: list[dict]) -> dict:
    return {
        "id": thread_id,
        "historyId": str(history_id),
        "messages": messages,
    }


def history_record(history_id: int, added: list[dict]) -> dict:
    """One ``history.list`` record: messagesAdded for a given historyId."""
    msgs = [{"id": a["id"], "threadId": a["threadId"]} for a in added]
    return {
        "id": str(history_id),
        "messages": msgs,
        "messagesAdded": [{"message": {"id": a["id"], "threadId": a["threadId"],
                                       "labelIds": a["labelIds"]}} for a in added],
    }


def profile_dto(email: str, messages_total: int, threads_total: int, history_id: int) -> dict:
    return {
        "emailAddress": email,
        "messagesTotal": messages_total,
        "threadsTotal": threads_total,
        "historyId": str(history_id),
    }
