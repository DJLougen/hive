"""Reading and writing interval lists like ``"1-3, 5-5, 8-10"``."""

from __future__ import annotations

from .model import Interval

SEPARATOR = ","
RANGE = "-"


def parse_intervals(text: str) -> list[Interval]:
    """Parse a comma-separated list of ranges; whitespace is ignored."""
    intervals = []
    for part in text.replace(" ", "").split(SEPARATOR):
        if not part:
            continue
        start, sep, end = part.partition(RANGE)
        if not sep:
            end = start
        intervals.append(Interval(int(start), int(end)))
    return intervals


def format_intervals(intervals: list[Interval]) -> str:
    """Render intervals back into the ``"1-3, 5-5"`` form."""
    return SEPARATOR.join(str(iv) for iv in intervals)
