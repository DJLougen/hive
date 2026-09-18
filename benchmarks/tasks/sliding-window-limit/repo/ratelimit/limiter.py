"""A per-key call cap over a time window."""

from __future__ import annotations


class RateLimitExceeded(Exception):
    """Raised when a key has spent its allowance in the current window."""


class RateLimiter:
    """Cap each key at ``limit`` calls per ``window_s`` seconds.

    The window is anchored to the clock: calls are bucketed into fixed
    intervals of ``window_s`` and the cap applies within each bucket.
    """

    def __init__(self, limit: int, window_s: float) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if window_s <= 0:
            raise ValueError("window_s must be positive")
        self.limit = limit
        self.window_s = window_s
        self._counts: dict[str, tuple[int, int]] = {}  # key -> (bucket, count)

    def _bucket(self, now: float) -> int:
        return int(now // self.window_s)

    def check(self, key: str, now: float) -> bool:
        """Would a call by ``key`` at ``now`` be allowed? Does not spend."""
        bucket = self._bucket(now)
        seen = self._counts.get(key)
        if seen is None or seen[0] != bucket:
            return True
        return seen[1] < self.limit

    def call(self, key: str, now: float) -> float:
        """Record a call by ``key`` at ``now``; return the retry-after in seconds.

        Raises ``RateLimitExceeded`` when the cap for the current bucket is
        already spent.
        """
        bucket = self._bucket(now)
        seen = self._counts.get(key)
        count = 0 if seen is None or seen[0] != bucket else seen[1]
        if count >= self.limit:
            retry = (bucket + 1) * self.window_s - now
            raise RateLimitExceeded(f"{key}: retry in {retry:.3f}s")
        self._counts[key] = (bucket, count + 1)
        return 0.0

    def active_keys(self) -> int:
        """How many keys currently have a nonzero count."""
        return len(self._counts)
