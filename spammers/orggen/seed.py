"""Deterministic RNG facade.

Every random choice in OrgGen must go through ``RunRandom`` so that
re-running with the same ``(size, runtime, seed)`` produces byte-identical
output. Never import ``random`` or ``numpy.random`` elsewhere.
"""
from __future__ import annotations

import hashlib
import random
from typing import Iterable, Sequence, TypeVar

T = TypeVar("T")


class RunRandom:
    """A namespaced deterministic RNG.

    Sub-namespaces are derived via SHA-256(parent_seed || namespace).
    This lets independent passes (people-gen vs message-gen) draw
    without colliding, while remaining fully deterministic on the
    parent seed.
    """

    def __init__(self, seed: int, namespace: str = "root") -> None:
        self._seed = seed
        self._namespace = namespace
        ns_seed = self._derive_seed(seed, namespace)
        self._rng = random.Random(ns_seed)

    @staticmethod
    def _derive_seed(parent: int, namespace: str) -> int:
        h = hashlib.sha256(f"{parent}:{namespace}".encode("utf-8")).digest()
        # take the first 8 bytes as a signed int64-ish seed
        return int.from_bytes(h[:8], "big", signed=False)

    def sub(self, namespace: str) -> "RunRandom":
        return RunRandom(self._seed, f"{self._namespace}/{namespace}")

    # --- Wrapped primitives ---

    def random(self) -> float:
        return self._rng.random()

    def randint(self, a: int, b: int) -> int:
        return self._rng.randint(a, b)

    def uniform(self, a: float, b: float) -> float:
        return self._rng.uniform(a, b)

    def choice(self, seq: Sequence[T]) -> T:
        return self._rng.choice(seq)

    def choices(self, seq: Sequence[T], k: int, weights: Sequence[float] | None = None) -> list[T]:
        return self._rng.choices(seq, weights=weights, k=k)

    def sample(self, seq: Sequence[T], k: int) -> list[T]:
        return self._rng.sample(seq, k)

    def shuffle(self, seq: list[T]) -> None:
        self._rng.shuffle(seq)

    def weighted_pick(self, items: Sequence[tuple[T, float]]) -> T:
        values, weights = zip(*items)
        return self._rng.choices(values, weights=list(weights), k=1)[0]

    def gauss(self, mu: float, sigma: float) -> float:
        return self._rng.gauss(mu, sigma)

    def bool_with_prob(self, p: float) -> bool:
        return self._rng.random() < p
