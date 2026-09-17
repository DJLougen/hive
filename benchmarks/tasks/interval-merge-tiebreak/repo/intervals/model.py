"""The interval itself."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Interval:
    """A closed range over the integers: ``start`` and ``end`` are both covered.

    ``Interval(5, 5)`` is the single point 5, which is a real interval rather
    than an empty one — a booking that takes no time still exists.
    """

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"interval end {self.end} precedes start {self.start}")

    @property
    def length(self) -> int:
        """How many integers the interval covers."""
        return self.end - self.start + 1

    def covers(self, point: int) -> bool:
        return self.start <= point <= self.end

    def __str__(self) -> str:
        return f"{self.start}-{self.end}"


def covers(intervals: list[Interval], point: int) -> bool:
    """Is ``point`` covered by any of ``intervals``?"""
    return any(iv.covers(point) for iv in intervals)


def touches_or_overlaps(a: Interval, b: Interval) -> bool:
    """Do ``a`` and ``b`` belong to the same run of covered integers?

    True when they share a point, and also when they are merely adjacent —
    there is no uncovered integer between them, so a merged interval is the
    honest description of what is covered. ``a`` must not start after ``b``.
    """
    return b.start <= a.end
