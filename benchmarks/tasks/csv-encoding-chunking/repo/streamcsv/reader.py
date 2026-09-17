"""Reading typed records out of a chunked CSV stream."""

from __future__ import annotations

from typing import Iterable, Iterator

from .parse import DELIMITER, parse_row
from .rows import iter_rows


def iter_chunks(text: str, size: int = 64) -> Iterator[str]:
    """Slice text into chunks of at most ``size`` characters."""
    if size < 1:
        raise ValueError("chunk size must be at least 1")
    for start in range(0, len(text), size):
        yield text[start:start + size]


def read_records(source: str | Iterable[str], *, chunk_size: int = 64,
                 delimiter: str = DELIMITER) -> list[dict[str, str]]:
    """Read a CSV stream into dicts keyed by the header row.

    ``source`` may be the whole text or a stream of chunks; either way the
    records are the same, because a record's boundaries are decided by quoting
    rather than by where the chunks fall.
    """
    chunks = iter_chunks(source, chunk_size) if isinstance(source, str) else source
    rows = [parse_row(r, delimiter=delimiter) for r in iter_rows(chunks)]
    rows = [r for r in rows if r and not (len(r) == 1 and r[0] == "")]
    if not rows:
        return []
    header = rows[0]
    return [dict(zip(header, r, strict=False)) for r in rows[1:]]
