"""Merging a bag of intervals into the runs of integers they cover."""

from __future__ import annotations

from .model import Interval, touches_or_overlaps


def merge(intervals: list[Interval]) -> list[Interval]:
    """Return the minimal sorted list of intervals covering the same integers.

    Input order and duplication do not matter; the output is canonical, so two
    callers that describe the same coverage get the same answer.
    """
    out: list[Interval] = []
    for iv in sorted(intervals):
        if out and touches_or_overlaps(out[-1], iv):
            current = out[-1]
            out[-1] = Interval(current.start, max(current.end, iv.end))
        else:
            out.append(iv)
    return out
