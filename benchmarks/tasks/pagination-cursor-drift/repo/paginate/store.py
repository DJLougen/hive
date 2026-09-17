"""A tiny ordered record store that pages through the query above."""

from __future__ import annotations

from operator import itemgetter
from typing import Any, Iterator

from .query import page


class RecordStore:
    """Keeps records and hands out pages of them, newest sort key last."""

    def __init__(self, records: list[dict[str, Any]] | None = None) -> None:
        self._records: list[dict[str, Any]] = list(records or [])

    def add(self, **record: Any) -> dict[str, Any]:
        self._records.append(record)
        return record

    def all(self) -> list[dict[str, Any]]:
        return list(self._records)

    def page(self, *, cursor: str | None = None, size: int = 3) -> dict[str, Any]:
        return page(self._records, cursor=cursor, size=size)

    def walk(self, size: int = 3) -> Iterator[dict[str, Any]]:
        """Yield every record exactly once, following the cursors to the end."""
        cursor = None
        while True:
            chunk = self.page(cursor=cursor, size=size)
            yield from chunk["items"]
            cursor = chunk["next_cursor"]
            if cursor is None:
                return
