"""Token-bucket rate limiter for the four mocks.

Each provider has its own model — Slack tiers, GitHub 5000/hr, Discord
per-bucket, Gmail per-user-per-second — but they all reduce to a token
bucket keyed on (provider, identity, method/bucket).

In-process state (per mock process). Multi-process deployments would
need a Redis backend; for the spammer suite, single-process per mock
is the design.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass
class Bucket:
    capacity: float
    refill_per_sec: float
    tokens: float
    last_refill: float          # wall-clock seconds

    def take(self, n: float, *, now: Optional[float] = None) -> Tuple[bool, float]:
        """Try to consume ``n`` tokens. Returns ``(ok, retry_after_seconds)``.

        ``retry_after_seconds`` is 0.0 when ok=True; otherwise the wall time
        until ``n`` tokens are available.
        """
        t = time.monotonic() if now is None else now
        elapsed = t - self.last_refill
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_sec)
            self.last_refill = t
        if self.tokens >= n:
            self.tokens -= n
            return True, 0.0
        deficit = n - self.tokens
        retry = deficit / self.refill_per_sec if self.refill_per_sec > 0 else float("inf")
        return False, retry

    def reset_at(self) -> float:
        """Wall-clock seconds until the bucket fully refills."""
        if self.tokens >= self.capacity or self.refill_per_sec <= 0:
            return 0.0
        return (self.capacity - self.tokens) / self.refill_per_sec


class RateLimiter:
    """Keyed bucket store. ``acquire(key, cost)`` is async-safe."""

    def __init__(self) -> None:
        self._buckets: Dict[str, Bucket] = {}
        self._lock = asyncio.Lock()

    def configure(
        self,
        key: str,
        *,
        capacity: float,
        refill_per_sec: float,
        initial_tokens: Optional[float] = None,
    ) -> Bucket:
        b = Bucket(
            capacity=capacity,
            refill_per_sec=refill_per_sec,
            tokens=capacity if initial_tokens is None else initial_tokens,
            last_refill=time.monotonic(),
        )
        self._buckets[key] = b
        return b

    def get_or_configure(
        self,
        key: str,
        *,
        capacity: float,
        refill_per_sec: float,
    ) -> Bucket:
        b = self._buckets.get(key)
        if b is None:
            b = self.configure(key, capacity=capacity, refill_per_sec=refill_per_sec)
        return b

    async def take(
        self,
        key: str,
        *,
        capacity: float,
        refill_per_sec: float,
        cost: float = 1.0,
    ) -> Tuple[bool, float, Bucket]:
        async with self._lock:
            b = self.get_or_configure(
                key, capacity=capacity, refill_per_sec=refill_per_sec,
            )
            ok, retry = b.take(cost)
            return ok, retry, b


# ---------- Provider-specific helpers ----------

# Slack tier table (approximate; matches Slack's published guidance).
# Tier 1: ~1/min, Tier 2: ~20/min, Tier 3: ~50/min, Tier 4: ~100+/min.
# We model the sustained refill rate, with bursts allowed up to ``capacity``.
SLACK_TIER_CONFIG: Dict[str, Tuple[float, float]] = {
    # method-specific override: chat.postMessage is special — 1/sec per channel.
    "chat.postMessage": (5.0, 1.0),
    "users.info":        (100.0, 100.0 / 60.0),   # Tier 4 (~100/min)
    "users.list":        (10.0, 20.0 / 60.0),     # Tier 2 (~20/min)
    "conversations.info":   (50.0, 50.0 / 60.0),  # Tier 3 (~50/min)
    "conversations.list":   (10.0, 20.0 / 60.0),  # Tier 2 (~20/min)
    "conversations.history": (1.0, 1.0 / 60.0),   # Tier 1 (~1/min) — non-Marketplace (May 2025)
    "conversations.replies": (1.0, 1.0 / 60.0),   # Tier 1 (~1/min) — non-Marketplace (May 2025)
    "conversations.members": (100.0, 100.0 / 60.0),  # Tier 4 (~100/min)
    "team.info":         (10.0, 50.0 / 60.0),     # Tier 3 (~50/min)
    "auth.test":         (100.0, 100.0 / 60.0),   # Tier 4 (~100/min)
}


def slack_tier_for(method: str) -> Tuple[float, float]:
    """Return ``(capacity, refill_per_sec)`` for a Slack Web API method."""
    return SLACK_TIER_CONFIG.get(method, (60.0, 1.0))


# GitHub: 5000/hr per installation, REST API. Burst of 100.
def github_tier() -> Tuple[float, float]:
    return (100.0, 5000.0 / 3600.0)


# Discord: per-route bucket; we use a soft default and expose
# X-RateLimit-* headers based on the bucket state.
def discord_default_bucket() -> Tuple[float, float]:
    return (10.0, 50.0)         # 50/sec global default per-bucket (with 10 burst)


# Gmail: 250 quota units / sec / user. Most operations cost 5; messages.get costs 5.
def gmail_quota() -> Tuple[float, float]:
    return (250.0, 250.0)
