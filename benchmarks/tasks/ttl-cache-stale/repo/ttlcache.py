"""A minimal TTL cache."""

import time


class TTLCache:
    def __init__(self, ttl: float):
        self.ttl = ttl
        self._store: dict[str, tuple[object, float]] = {}

    def set(self, key: str, value) -> None:
        expires_at = time.monotonic() + self.ttl
        self._store[key] = (value, expires_at)

    def get(self, key: str):
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.time() > expires_at:
            del self._store[key]
            return None
        return value
