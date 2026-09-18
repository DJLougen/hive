"""A cache bounded by time-to-live and by least-recently-used capacity."""

from __future__ import annotations


class LruTtlCache:
    """Hold at most ``cap`` entries, each living at most ``ttl`` seconds.

    Entries are stored with the time they were set and evicted in insertion
    order when the cache is full — neither the TTL nor the access recency is
    consulted.
    """

    def __init__(self, cap: int, ttl: float) -> None:
        if cap < 1:
            raise ValueError("cap must be at least 1")
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        self.cap = cap
        self.ttl = ttl
        self._store: dict[str, tuple[object, float]] = {}  # key -> (value, set_at)

    def get(self, key: str, now: float) -> object | None:
        """Return the value for ``key``, or None if absent or expired."""
        entry = self._store.get(key)
        return entry[0] if entry is not None else None

    def set(self, key: str, value: object, now: float) -> None:
        """Store ``value`` under ``key`` set at ``now``."""
        if key not in self._store and len(self._store) >= self.cap:
            oldest = next(iter(self._store))  # insertion order, not recency
            del self._store[oldest]
        self._store[key] = (value, now)

    def __len__(self) -> int:
        return len(self._store)
