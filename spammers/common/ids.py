"""ID generators that look like the real providers.

- Slack team:    'T' + 8 uppercase alphanumerics
- Slack channel: 'C' + 8 uppercase alphanumerics
- Slack user:    'U' + 8 uppercase alphanumerics
- Slack app:     'A' + 8 uppercase alphanumerics
- Slack ts:      f"{secs}.{micros:06d}"
- Slack bot tok: 'xoxb-' + 12-15 digits + '-' + ... (~80 chars total)
- Slack signing: 32-char hex
- Discord snowflake: 64-bit int packed (timestamp_ms - 1420070400000) << 22 | random
- GitHub installation: 7-digit int
- GitHub app id: 5-6 digit int
- GitHub access token: 'ghs_' + 36 chars
- Gmail message id: 16-char hex
- Gmail history id: monotonic int
"""
from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone
from typing import Optional


_UPPER_ALNUM = string.ascii_uppercase + string.digits
_LOWER_ALNUM = string.ascii_lowercase + string.digits
_HEX = string.hexdigits.lower()[:16]


def _rand(charset: str, n: int) -> str:
    return "".join(secrets.choice(charset) for _ in range(n))


# ---- Slack ----

def slack_team_id() -> str:
    return "T" + _rand(_UPPER_ALNUM, 9)


def slack_user_id() -> str:
    return "U" + _rand(_UPPER_ALNUM, 9)


def slack_channel_id() -> str:
    return "C" + _rand(_UPPER_ALNUM, 9)


def slack_app_id() -> str:
    return "A" + _rand(_UPPER_ALNUM, 9)


def slack_bot_token() -> str:
    return f"xoxb-{_rand(string.digits, 12)}-{_rand(string.digits, 12)}-{_rand(_LOWER_ALNUM, 24)}"


def slack_user_token() -> str:
    return f"xoxp-{_rand(string.digits, 12)}-{_rand(string.digits, 12)}-{_rand(_LOWER_ALNUM, 24)}"


def slack_signing_secret() -> str:
    return _rand(_HEX, 32)


def slack_ts(when: datetime) -> str:
    """Slack ts: 'seconds.microseconds' with 6-digit micros."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    secs = int(when.timestamp())
    micros = when.microsecond
    return f"{secs}.{micros:06d}"


def slack_event_id() -> str:
    return "Ev" + _rand(_UPPER_ALNUM, 10)


def slack_client_id() -> str:
    return f"{_rand(string.digits, 11)}.{_rand(string.digits, 13)}"


def slack_client_secret() -> str:
    return _rand(_HEX, 32)


# ---- Discord ----

_DISCORD_EPOCH_MS = 1420070400000  # 2015-01-01T00:00:00Z


def discord_snowflake(when: Optional[datetime] = None) -> str:
    when = when or datetime.now(timezone.utc)
    ms = int(when.timestamp() * 1000) - _DISCORD_EPOCH_MS
    rand = secrets.randbits(22)
    return str((ms << 22) | rand)


def discord_bot_token() -> str:
    # Real shape: <user_id_b64>.<ts_b64>.<hmac_b64>
    a = secrets.token_urlsafe(18)[:24]
    b = secrets.token_urlsafe(4)[:6]
    c = secrets.token_urlsafe(20)[:27]
    return f"{a}.{b}.{c}"


# ---- GitHub ----

def github_app_id() -> int:
    return secrets.randbelow(900000) + 100000


def github_installation_id() -> int:
    return secrets.randbelow(90000000) + 10000000


def github_repo_id() -> int:
    return secrets.randbelow(900000000) + 100000000


def github_user_id() -> int:
    return secrets.randbelow(90000000) + 10000000


def github_installation_token() -> str:
    return "ghs_" + _rand(_LOWER_ALNUM + string.ascii_uppercase, 36)


def github_delivery_id() -> str:
    return f"{_rand(_HEX, 8)}-{_rand(_HEX, 4)}-{_rand(_HEX, 4)}-{_rand(_HEX, 4)}-{_rand(_HEX, 12)}"


def github_sha() -> str:
    return _rand(_HEX, 40)


def github_webhook_secret() -> str:
    return _rand(_HEX, 32)


# ---- Gmail ----

def gmail_message_id() -> str:
    return _rand(_HEX, 16)


def gmail_thread_id() -> str:
    return _rand(_HEX, 16)


def gmail_rfc822_id(domain: str) -> str:
    return f"<{_rand(_HEX, 16)}@{domain}>"


# ---- Google Calendar ----

# Real Google event ids are base32hex (digits + lowercase a-v), 26 chars.
_BASE32HEX = string.digits + "abcdefghijklmnopqrstuv"


def gcal_event_id() -> str:
    return _rand(_BASE32HEX, 26)


def gcal_ical_uid() -> str:
    return f"{_rand(_HEX, 8)}-{_rand(_HEX, 4)}-{_rand(_HEX, 4)}-{_rand(_HEX, 4)}-{_rand(_HEX, 12)}@google.com"


# ---- Notion ----

def notion_id() -> str:
    """Notion object ids are UUIDv4, rendered dashed (8-4-4-4-12)."""
    import uuid
    return str(uuid.uuid4())


def notion_token() -> str:
    """Notion internal-integration token: 'ntn_' + ~46 base62 chars."""
    return "ntn_" + _rand(_UPPER_ALNUM.lower() + _UPPER_ALNUM, 46)


def notion_verification_token() -> str:
    return "secret_" + _rand(_LOWER_ALNUM + string.ascii_uppercase, 43)


# ---- OAuth code/state ----

def oauth_code() -> str:
    return secrets.token_urlsafe(32)


def oauth_state() -> str:
    return secrets.token_urlsafe(24)
