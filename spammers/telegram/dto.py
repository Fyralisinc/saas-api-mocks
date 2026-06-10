"""Wire shapes for the Telegram mock — the MTProto/Telethon method contract.

These reproduce the TL object SEMANTICS the consumer (a Telethon-based client)
parses, even though the binary transport is substituted by HTTP/WS:

  - ``message_dto``        — a TL ``message`` (id, peer_id, from_id, date/edit_date
                             EPOCH SECONDS, out, message text). ``from_id`` is a TL
                             Peer or NULL (channel-broadcast / self-sent: no sender).
  - ``dialog_dto``         — a Telethon Dialog descriptor (dialog_id, kind,
                             access_hash, title) — the iter_dialogs shape.
  - ``self_user_dto``      — the get_me / users.getFullUser self ``user``.
  - ``peer``               — the TL Peer constructor for a dialog/sender.
  - ``rpc_error``          — the MTProto ``rpc_error`` envelope {error_code, error_message}.
  - ``update_new_message`` / ``update_edit_message`` — the live update frames.

Timestamps are EPOCH SECONDS (the MTProto wire encoding; Telethon surfaces aware
datetimes and a faithful client flattens back to epoch seconds).
"""
from __future__ import annotations

from typing import Any, Optional


def peer(dialog_id: int, dialog_kind: str) -> dict[str, Any]:
    """The TL Peer constructor for a dialog/sender id of the given kind."""
    if dialog_kind == "channel":
        return {"_": "peerChannel", "channel_id": int(dialog_id)}
    if dialog_kind == "user":
        return {"_": "peerUser", "user_id": int(dialog_id)}
    return {"_": "peerChat", "chat_id": int(dialog_id)}


def message_dto(
    *,
    message_id: int,
    dialog_id: int,
    dialog_kind: str,
    date_ts: int,
    edit_date_ts: Optional[int],
    text: str,
    out: bool,
    from_user_id: Optional[int],
) -> dict[str, Any]:
    """A TL ``message`` as Telethon would deserialize it (epoch-seconds dates).

    ``from_id`` is a ``peerUser`` Peer for a user sender, or NULL for a
    channel-broadcast / self-sent message that carries no ``from_id`` (the sender
    is implicit; direction is carried by ``out``). ``edit_date`` is NULL until the
    message is edited.
    """
    return {
        "_": "message",
        "id": int(message_id),
        "peer_id": peer(dialog_id, dialog_kind),
        "from_id": ({"_": "peerUser", "user_id": int(from_user_id)}
                    if from_user_id is not None else None),
        "date": int(date_ts),
        "edit_date": (int(edit_date_ts) if edit_date_ts is not None else None),
        "message": text or "",
        "out": bool(out),
    }


def dialog_dto(*, dialog_id: int, dialog_kind: str,
               access_hash: Optional[int], title: Optional[str]) -> dict[str, Any]:
    """A Telethon Dialog descriptor (iter_dialogs shape)."""
    return {
        "dialog_id": int(dialog_id),
        "dialog_kind": dialog_kind,
        "access_hash": (int(access_hash) if access_hash is not None else None),
        "title": title,
    }


def self_user_dto(*, user_id: int, username: Optional[str],
                  phone: Optional[str]) -> dict[str, Any]:
    """The get_me / users.getFullUser self ``user`` (id always; username/phone
    nullable). ``self``/``bot`` flags included for faithfulness."""
    return {
        "_": "user",
        "id": int(user_id),
        "is_self": True,
        "bot": False,
        "username": username,
        "phone": phone,
    }


def rpc_error(error_code: int, error_message: str) -> dict[str, Any]:
    """The MTProto ``rpc_error`` envelope. Telethon raises an RPCError subclass
    keyed on ``error_message`` (e.g. FLOOD_WAIT_X → FloodWaitError(seconds=X),
    AUTH_KEY_UNREGISTERED → unauthorized, PEER_ID_INVALID → bad peer)."""
    return {"_": "rpc_error", "error_code": int(error_code),
            "error_message": str(error_message)}


def update_new_message(*, message: dict[str, Any], pts: int,
                       dialog: dict[str, Any]) -> dict[str, Any]:
    """An ``updateNewMessage`` push frame (carries a full message + the pts cursor
    + the resolved dialog context the gateway worker attaches)."""
    return {"_": "updateNewMessage", "message": message,
            "pts": int(pts), "pts_count": 1, "dialog": dialog}


def update_edit_message(*, message: dict[str, Any], pts: int,
                        dialog: dict[str, Any]) -> dict[str, Any]:
    """An ``updateEditMessage`` push frame (the edited message carries a fresh
    edit_date with the SAME message id → the consumer re-observes)."""
    return {"_": "updateEditMessage", "message": message,
            "pts": int(pts), "pts_count": 1, "dialog": dialog}
