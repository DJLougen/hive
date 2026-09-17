"""Interval merging for calendar bookings."""


def merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping *or touching* intervals; return sorted list."""
    if not intervals:
        return []
    ivs = sorted(intervals)
    merged = [list(ivs[0])]
    for start, end in ivs[1:]:
        last = merged[-1]
        if start >= last[1]:
            merged.append([start, end])
        else:
            last[1] = max(last[1], end)
    return [tuple(m) for m in merged]
