"""Turning a stream of chunks into logical records."""

from __future__ import annotations

from typing import Iterable, Iterator

from .scan import NEWLINE, QUOTE, scan_chunk


def iter_rows(chunks: Iterable[str], *, quote: str = QUOTE,
              newline: str = NEWLINE) -> Iterator[str]:
    """Yield logical records, without their terminator, from text chunks.

    The unit of work is a record, not a chunk: how the caller happened to slice
    the text must not change which records come out.
    """
    for chunk in chunks:
        if not chunk:
            continue
        yield from scan_chunk(chunk, quote=quote, newline=newline)
