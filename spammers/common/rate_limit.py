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

# Slack tier table. Published guidance:
#   Tier 1 ~1/min, Tier 2 ~20/min, Tier 3 ~50/min, Tier 4 ~100+/min.
# Capacity (burst) is set to roughly the per-minute number; refill is the
# sustained per-second rate. chat.postMessage is "Special" (1 msg/sec/channel);
# auth.test is "Special" (hundreds/min).
SLACK_TIER_CONFIG: Dict[str, Tuple[float, float]] = {
    "chat.postMessage": (1.0, 1.0),               # Special: ~1/sec per channel
    "chat.update":       (50.0, 50.0 / 60.0),     # Tier 3
    "chat.delete":       (50.0, 50.0 / 60.0),     # Tier 3
    "users.info":        (100.0, 100.0 / 60.0),   # Tier 4 (~100/min)
    "users.list":        (20.0, 20.0 / 60.0),     # Tier 2 (~20/min)
    "conversations.info":   (50.0, 50.0 / 60.0),  # Tier 3 (~50/min)
    "conversations.list":   (20.0, 20.0 / 60.0),  # Tier 2 (~20/min)
    "conversations.history": (50.0, 50.0 / 60.0),  # Tier 3 — Marketplace/internal
    "conversations.replies": (50.0, 50.0 / 60.0),  # Tier 3 — Marketplace/internal
    "conversations.members": (100.0, 100.0 / 60.0),  # Tier 4 (~100/min)
    "team.info":         (50.0, 50.0 / 60.0),     # Tier 3 (~50/min)
    "auth.test":         (300.0, 300.0 / 60.0),   # Special: hundreds/min
}

# Post-2025-05-29: non-Marketplace apps are capped to 1 req/min on these two
# methods (and limit<=15 objects/page, enforced in the route).
_NON_MARKETPLACE_TIER1 = {"conversations.history", "conversations.replies"}


def slack_tier_for(method: str, app_distribution: str = "marketplace") -> Tuple[float, float]:
    """Return ``(capacity, refill_per_sec)`` for a Slack Web API method.

    ``app_distribution`` ("marketplace" | "non_marketplace") selects the
    post-2025 1-req/min cap on conversations.history/replies for non-Marketplace
    apps; everything else is app-class-independent.
    """
    if app_distribution != "marketplace" and method in _NON_MARKETPLACE_TIER1:
        return (1.0, 1.0 / 60.0)
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
