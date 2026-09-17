"""Smoke tests: the public API happy path. Not the grading criterion."""

from __future__ import annotations

from intervals import Interval, format_intervals, merge, parse_intervals

# Well-separated ranges with real gaps, so nothing here depends on adjacency.
SPREAD = [Interval(1, 3), Interval(9, 12), Interval(20, 20)]


def test_overlapping_intervals_become_one():
    assert merge([Interval(1, 5), Interval(3, 9)]) == [Interval(1, 9)]


def test_disjoint_intervals_are_left_alone():
    assert merge(SPREAD) == SPREAD


def test_output_is_sorted_regardless_of_input_order():
    assert merge(list(reversed(SPREAD))) == SPREAD


def test_a_single_point_interval_is_a_real_interval():
    assert Interval(5, 5).length == 1
    assert merge([Interval(5, 5)]) == [Interval(5, 5)]


def test_round_trip_through_text():
    assert format_intervals(parse_intervals("1-3, 9-12")) == "1-3,9-12"


def test_parse_accepts_a_bare_point():
    assert parse_intervals("4") == [Interval(4, 4)]


def test_reversed_bounds_are_rejected():
    import pytest

    with pytest.raises(ValueError):
        Interval(5, 4)
