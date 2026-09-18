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


# --- Spec coverage: the issue's stated requirements, not just the happy path.
# These fail on the shipped repo; the held-out oracle checks the edge cases.


def test_adjacent_ranges_merge_into_one_run():
    # The issue: 1-3 and 4-6 leave no integer uncovered, so they are one run.
    assert merge([Interval(1, 3), Interval(4, 6)]) == [Interval(1, 6)]


def test_a_point_interval_joins_the_run_it_touches():
    assert merge([Interval(5, 5), Interval(6, 7)]) == [Interval(5, 7)]
    assert merge([Interval(1, 4), Interval(5, 5)]) == [Interval(1, 5)]


def test_gaps_returns_the_uncovered_runs_between_coverage():
    from intervals.gaps import gaps

    assert gaps([Interval(1, 3), Interval(7, 9)]) == [Interval(4, 6)]
    assert gaps([Interval(1, 3), Interval(4, 9)]) == []


def test_gaps_treats_a_single_missing_integer_as_a_hole():
    from intervals.gaps import gaps

    assert gaps([Interval(1, 1), Interval(3, 3)]) == [Interval(2, 2)]
