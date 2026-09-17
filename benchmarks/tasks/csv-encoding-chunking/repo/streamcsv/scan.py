"""Finding record boundaries in a chunk of text."""

from __future__ import annotations

QUOTE = '"'
NEWLINE = "\n"


def scan_chunk(chunk: str, *, quote: str = QUOTE, newline: str = NEWLINE) -> list[str]:
    """Split one chunk into records, honouring quoted fields.

    A record ends at a ``newline`` that is not inside a quoted field, so a
    quoted field may contain newlines. Inside a quoted field two consecutive
    ``quote`` characters stand for one literal quote.
    """
    records: list[str] = []
    buffer: list[str] = []
    in_quotes = False
    i = 0
    while i < len(chunk):
        char = chunk[i]
        if char == quote:
            if in_quotes and i + 1 < len(chunk) and chunk[i + 1] == quote:
                buffer.append(quote)
                buffer.append(quote)
                i += 2
                continue
            in_quotes = not in_quotes
            buffer.append(char)
        elif char == newline and not in_quotes:
            records.append("".join(buffer))
            buffer = []
        else:
            buffer.append(char)
        i += 1
    if buffer:
        records.append("".join(buffer))
    return records
