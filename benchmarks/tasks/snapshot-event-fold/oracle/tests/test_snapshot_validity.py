"""Held-out grading tests for snapshot validation."""

from __future__ import annotations

import pytest

from eventfold import Snapshot, load_state


def test_a_zero_coverage_snapshot_behaves_like_no_snapshot():
    assert load_state([1, 2, 3], Snapshot(state=0, covered=0)) == 6


def test_a_full_coverage_snapshot_returns_its_state():
    snap = Snapshot(state=6, covered=3)
    assert load_state([1, 2, 3], snap) == 6


def test_the_tail_after_the_snapshot_folds_in_order():
    snap = Snapshot(state=0, covered=2)
    # Events 3..5 are -1, -1, +10: order matters only to the sum, but the
    # covered prefix must not be re-applied.
    assert load_state([5, 5, -1, -1, 10], snap) == 8


def test_a_snapshot_from_a_different_log_is_rejected():
    # covered=4 but the log has 3 events — the snapshot claims history that
    # never happened here.
    with pytest.raises(ValueError):
        load_state([1, 2, 3], Snapshot(state=0, covered=4))


def test_negative_coverage_is_rejected():
    with pytest.raises(ValueError):
        load_state([1, 2, 3], Snapshot(state=0, covered=-1))


def test_an_empty_log_with_a_valid_snapshot():
    assert load_state([], Snapshot(state=42, covered=0)) == 42


def test_state_is_rebuilt_not_mutated():
    snap = Snapshot(state=3, covered=2)
    first = load_state([1, 2, 10], snap)
    second = load_state([1, 2, 10], snap)
    assert first == second == 13
