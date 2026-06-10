"""Wire shapes for the Signal mock — the REAL signal-cli JSON-RPC envelope shapes.

Per the user-approved transport decision (B+C), the transport is a Telegram-style
method-contract shim (HTTP reads + WS gateway) but the PAYLOADS are the genuine
signal-cli ``receive`` envelope shapes a consumer parses — verified against the
official signal-cli man page (signal-cli-jsonrpc.5) + the Java source records
(JsonMessageEnvelope / JsonDataMessage / JsonGroupInfo / JsonSyncMessage):

  - ``envelope``         — a signal-cli received-message envelope: ``source``,
                           ``sourceNumber``, ``sourceUuid``, ``sourceName``,
                           ``sourceDevice``, ``timestamp`` (epoch MILLISECONDS),
                           and EXACTLY ONE body — ``dataMessage`` (an inbound
                           direct/group message) or ``syncMessage.sentMessage`` (an
                           own/linked-device-sent message; the ``out`` analog).
                           A group message carries ``dataMessage.groupInfo``
                           (``groupId`` base64, ``groupName``, ``revision``,
                           ``type``).
  - ``thread_descriptor``— a unified backfill thread descriptor
                           (``thread_id`` = contact uuid | base64 groupId,
                           ``thread_kind`` direct/group, ``thread_title``) — the
                           ``iter_threads`` shape the planner shards on.
  - ``account_dto``      — the linked account identity (``me`` probe).
  - ``rpc_error``        — a JSON-RPC 2.0 error envelope (signal-cli uses the
                           standard -32xxx codes); ``data.signal_code`` carries the
                           Fyralis ``SignalApiError`` code the consumer branches on.

Signal identifies a message by its sender-set ``timestamp`` in MILLISECONDS — there
is no separate integer message id, so ``timestamp`` IS the per-thread message id +
the backward-walk cursor + the dedup grain.
"""
from __future__ import annotations

from typing import Any, Optional

# JSON-RPC 2.0 standard error codes (signal-cli has no custom application codes).
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603


def group_info(*, group_id: str, group_name: Optional[str], revision: int,
               msg_type: str = "DELIVER") -> dict[str, Any]:
    """A signal-cli ``dataMessage.groupInfo`` (JsonGroupInfo): base64 ``groupId`` +
    ``groupName`` + ``revision`` + ``type`` (DELIVER for a normal message)."""
    return {"groupId": group_id, "groupName": group_name,
            "revision": int(revision), "type": msg_type}


def _data_message(*, ts_ms: int, body: str,
                  gi: Optional[dict[str, Any]]) -> dict[str, Any]:
    dm: dict[str, Any] = {
        "timestamp": int(ts_ms),
        "message": body or "",
        "expiresInSeconds": 0,
        "viewOnce": False,
        "mentions": [],
        "attachments": [],
    }
    if gi is not None:
        dm["groupInfo"] = gi
    return dm


def envelope(
    *,
    ts_ms: int,
    body: str,
    out: bool,
    self_number: str,
    self_uuid: str,
    sender_uuid: Optional[str],
    sender_number: Optional[str],
    sender_name: Optional[str],
    thread_kind: str,
    group_id: Optional[str] = None,
    group_name: Optional[str] = None,
    group_revision: Optional[int] = None,
    direct_peer_uuid: Optional[str] = None,
    direct_peer_number: Optional[str] = None,
) -> dict[str, Any]:
    """A signal-cli received-message envelope as the daemon would deliver it.

    INBOUND (``out`` False) → a ``dataMessage``: the envelope ``source*`` fields are
    the SENDER; a group message also carries ``dataMessage.groupInfo``.

    OWN/OUTGOING (``out`` True) → a ``syncMessage.sentMessage``: the envelope
    ``source*`` fields are the LINKED ACCOUNT (self), and ``sentMessage`` carries the
    ``destination*`` (the direct peer) — a group sentMessage carries ``groupInfo``
    instead. Own messages are the ``out=True`` rows the live gateway skips.
    """
    gi = None
    if thread_kind == "group" and group_id is not None:
        gi = group_info(group_id=group_id, group_name=group_name,
                        revision=int(group_revision or 0))

    if out:
        # syncMessage.sentMessage — the linked account sent this (from any device).
        sent: dict[str, Any] = {
            "timestamp": int(ts_ms),
            "message": body or "",
            "expiresInSeconds": 0,
            "viewOnce": False,
        }
        if gi is not None:
            sent["groupInfo"] = gi
        else:
            # a direct sent message carries the destination (the other party)
            sent["destination"] = direct_peer_number
            sent["destinationNumber"] = direct_peer_number
            sent["destinationUuid"] = direct_peer_uuid
        return {
            "source": self_number,
            "sourceNumber": self_number,
            "sourceUuid": self_uuid,
            "sourceName": None,
            "sourceDevice": 1,
            "timestamp": int(ts_ms),
            "syncMessage": {"sentMessage": sent},
        }

    # inbound dataMessage — the sender is in source*.
    return {
        "source": sender_number,
        "sourceNumber": sender_number,
        "sourceUuid": sender_uuid,
        "sourceName": sender_name,
        "sourceDevice": 1,
        "timestamp": int(ts_ms),
        "dataMessage": _data_message(ts_ms=ts_ms, body=body, gi=gi),
    }


def thread_descriptor(*, thread_id: str, thread_kind: str,
                      thread_title: Optional[str]) -> dict[str, Any]:
    """The unified ``iter_threads`` descriptor the planner shards on (a merge of
    signal-cli's separate ``listContacts`` [direct] + ``listGroups`` [group])."""
    return {"thread_id": thread_id, "thread_kind": thread_kind,
            "thread_title": thread_title}


def account_dto(*, number: str, uuid: str,
                username: Optional[str]) -> dict[str, Any]:
    """The linked account identity (the ``me`` connectivity + credential probe;
    signal-cli's ``listAccounts``/``getUserStatus`` self shape)."""
    return {"number": number, "uuid": uuid, "username": username}


def rpc_error(*, http_code: int, jsonrpc_code: int, signal_code: str,
              message: str, retry_after: Optional[int] = None) -> dict[str, Any]:
    """A JSON-RPC 2.0 error envelope. signal-cli surfaces failures as
    ``{"jsonrpc":"2.0","error":{"code":-32xxx,"message":...,"data":...},"id":null}``
    (only the five standard codes; no custom ones). ``data.signal_code`` carries the
    Fyralis ``SignalApiError`` code the consumer keys on; ``data.retry_after`` is
    present on a rate-limit (the server-chosen wait)."""
    data: dict[str, Any] = {"signal_code": signal_code, "http_status": http_code}
    if retry_after is not None:
        data["retry_after"] = int(retry_after)
    return {"jsonrpc": "2.0",
            "error": {"code": int(jsonrpc_code), "message": message, "data": data},
            "id": None}
