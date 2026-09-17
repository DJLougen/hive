"""Held-out grading tests for the gaps between covered runs."""

from __future__ import annotations

import pytest

from intervals import Interval, merge, parse_intervals
from intervals.gaps import gaps


def test_a_single_gap_between_two_runs():
    assert gaps([Interval(1, 3), Interval(7, 9)]) == [Interval(4, 6)]


def test_contiguous_coverage_has_no_gaps():
    assert gaps([Interval(1, 3), Interval(4, 9)]) == []
    assert gaps([Interval(1, 9)]) == []


def test_a_lone_missing_integer_is_a_one_length_gap():
    assert gaps([Interval(1, 1), Interval(3, 3)]) == [Interval(2, 2)]
    assert gaps([Interval(1, 2), Interval(4, 5), Interval(7, 8)]) == [
        Interval(3, 3), Interval(6, 6)]


def test_the_outside_of_the_coverage_is_not_a_gap():
    # Nothing is covered below 5 or above 5, but a gap is a hole *in* the
    # coverage, never the empty space around it.
    assert gaps([Interval(5, 5)]) == []
    assert gaps([Interval(10, 20)]) == []
    assert gaps([]) == []


def test_the_input_may_be_unmerged_and_repeated():
    messy = parse_intervals("9-9, 1-3, 4-6, 6-6, 7-9, 20-21")
    # Covered: 1..9 and 20..21 -> the only hole is 10..19.
    assert merge(messy) == [Interval(1, 9), Interval(20, 21)]
    assert gaps(messy) == [Interval(10, 19)]


def test_gaps_are_sorted_and_do_not_touch_each_other():
    gaps_found = gaps([Interval(0, 0), Interval(2, 2), Interval(4, 4), Interval(6, 10)])
    assert gaps_found == [Interval(1, 1), Interval(3, 3), Interval(5, 5)]
    for left, right in zip(gaps_found, gaps_found[1:]):
        assert right.start > left.end + 1


def test_gaps_of_a_single_run_that_is_split_by_a_hole_at_an_endpoint():
    assert gaps([Interval(1, 3), Interval(5, 7), Interval(9, 9)]) == [
        Interval(4, 4), Interval(8, 8)]
