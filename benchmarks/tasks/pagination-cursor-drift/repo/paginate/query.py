"""The paging query itself."""

from __future__ import annotations

from operator import itemgetter
from typing import Any, Callable, Iterable, Sequence

from .cursor import decode_cursor, encode_cursor


def _order_key(sort_key: Callable[[Any], Any], tiebreak: Callable[[Any], Any]):
    return lambda row: (sort_key(row), tiebreak(row))


def _resume_after(row: Any, key: Any, tie: Any, sort_key, tiebreak) -> bool:
    """Is ``row`` after the position the cursor was cut at?"""
    row_key = sort_key(row)
    if row_key != key:
        return row_key > key
    if tie is None:
        # The cursor named the sort key only: resume at the next group.
        return row_key > key
    return tiebreak(row) >= tie


def page(records: Iterable[Any], *, cursor: str | None = None, size: int = 3,
         sort_key: Callable[[Any], Any] = itemgetter("created_at"),
         tiebreak: Callable[[Any], Any] = itemgetter("id")) -> dict[str, Any]:
    """One page of ``records``, ordered by ``(sort_key, tiebreak)``.

    Returns ``{"items": [...], "next_cursor": str | None}``; ``next_cursor`` is
    ``None`` when the page is the last one, so a caller can page until it runs
    out instead of guessing how many pages there are.
    """
    if size < 1:
        raise ValueError("page size must be at least 1")
    ordered: Sequence[Any] = sorted(records, key=_order_key(sort_key, tiebreak))

    if cursor is not None:
        key, tie = decode_cursor(cursor)
        ordered = [r for r in ordered if _resume_after(r, key, tie, sort_key, tiebreak)]

    window = list(ordered[:size])
    next_cursor = None
    if window and len(ordered) > size:
        last = window[-1]
        next_cursor = encode_cursor(sort_key(last), tiebreak(last))
    return {"items": window, "next_cursor": next_cursor}
