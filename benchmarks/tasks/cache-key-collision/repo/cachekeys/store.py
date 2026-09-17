"""A tiny memo store."""

from __future__ import annotations

from typing import Any, Callable


class MemoStore:
    """Maps a key to the value computed for it, counting hits and misses."""

    def __init__(self) -> None:
        self._values: dict[str, Any] = {}
        self.hits = 0
        self.misses = 0

    def get_or_compute(self, key: str, compute: Callable[[], Any]) -> Any:
        if key in self._values:
            self.hits += 1
            return self._values[key]
        self.misses += 1
        value = compute()
        self._values[key] = value
        return value

    def clear(self) -> None:
        self._values.clear()
        self.hits = self.misses = 0

    def __len__(self) -> int:
        return len(self._values)

    def keys(self) -> list[str]:
        return list(self._values)
