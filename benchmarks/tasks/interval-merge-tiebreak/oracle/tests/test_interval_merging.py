"""Held-out grading tests for merging closed integer intervals."""

from __future__ import annotations

from intervals import Interval, covers, format_intervals, merge, parse_intervals


def _points(intervals):
    return sorted({p for iv in intervals for p in range(iv.start, iv.end + 1)})


def test_overlapping_intervals_merge():
    assert merge([Interval(1, 5), Interval(4, 9)]) == [Interval(1, 9)]


def test_touching_intervals_merge():
    # 3 and 4 are adjacent, so 1..6 is one unbroken run of covered integers.
    assert merge([Interval(1, 3), Interval(4, 6)]) == [Interval(1, 6)]
    assert merge([Interval(4, 6), Interval(1, 3)]) == [Interval(1, 6)]


def test_a_point_interval_joins_the_run_it_touches():
    assert merge([Interval(5, 5), Interval(6, 7)]) == [Interval(5, 7)]
    assert merge([Interval(1, 4), Interval(5, 5)]) == [Interval(1, 5)]
    assert merge([Interval(5, 5), Interval(3, 4)]) == [Interval(3, 5)]


def test_a_lone_point_is_kept():
    assert merge([Interval(5, 5)]) == [Interval(5, 5)]
    assert merge([Interval(1, 2), Interval(5, 5)]) == [Interval(1, 2), Interval(5, 5)]
    # A gap of exactly one uncovered integer keeps two runs apart.
    assert merge([Interval(1, 2), Interval(4, 5)]) == [Interval(1, 2), Interval(4, 5)]


def test_the_output_is_canonical_and_covers_everything():
    messy = parse_intervals("5-5, 1-3, 4-6, 6-6, 20-20, 20-21, 30-30, 8-8, 7-7")
    merged = merge(messy)
    assert merged == [Interval(1, 8), Interval(20, 21), Interval(30, 30)]
    assert [iv.start for iv in merged] == sorted(iv.start for iv in merged)
    assert _points(merged) == _points(messy)
    for point in (1, 3, 4, 6, 7, 8, 20, 21, 30):
        assert covers(merged, point), point
    # No two output intervals may touch or overlap.
    for left, right in zip(merged, merged[1:]):
        assert right.start > left.end + 1


def test_merging_is_idempotent():
    once = merge(parse_intervals("1-3, 4-6, 9-9, 10-11"))
    assert merge(once) == once


def test_parse_then_merge_collapses_an_adjacent_list():
    assert format_intervals(merge(parse_intervals("1-3, 4-6, 10-10, 11-12"))) == "1-6,10-12"
