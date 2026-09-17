"""Parsing one record into its fields."""

from __future__ import annotations

QUOTE = '"'
DELIMITER = ","


def parse_row(record: str, *, delimiter: str = DELIMITER, quote: str = QUOTE) -> list[str]:
    """Split one logical record (newlines already resolved) into fields."""
    fields: list[str] = []
    buffer: list[str] = []
    in_quotes = False
    i = 0
    while i < len(record):
        char = record[i]
        if char == quote:
            if in_quotes and i + 1 < len(record) and record[i + 1] == quote:
                buffer.append(quote)
                i += 2
                continue
            in_quotes = not in_quotes
        elif char == delimiter and not in_quotes:
            fields.append("".join(buffer))
            buffer = []
        else:
            buffer.append(char)
        i += 1
    fields.append("".join(buffer))
    return fields
