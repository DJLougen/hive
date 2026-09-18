"""Smoke tests: the disclosed spec. Fails on the shipped repo."""

from __future__ import annotations

import pytest

from eventfold import Snapshot, fold, load_state


def test_folding_the_whole_log_without_a_snapshot():
    assert load_state([1, 2, 3]) == 6


def test_a_snapshot_skips_the_events_it_covers():
    # Snapshot covers the first 2 events (1+2=3); only the tail folds on top.
    snap = Snapshot(state=3, covered=2)
    assert load_state([1, 2, 10, 20], snap) == 33


def test_a_mid_stream_snapshot_does_not_double_count():
    snap = Snapshot(state=6, covered=3)
    # Without validation the loader folds 1+2+3 again on top of 6.
    assert load_state([1, 2, 3, 4], snap) == 10


def test_a_snapshot_covering_more_than_the_log_is_rejected():
    snap = Snapshot(state=100, covered=5)
    with pytest.raises(ValueError):
        load_state([1, 2], snap)


def test_fold_adds_the_event():
    assert fold(10, 5) == 15


def test_a_negative_coverage_is_rejected():
    # The disclosed rule: covered must lie in [0, len(log)] — a negative count
    # is as invalid as one past the end.
    with pytest.raises(ValueError):
        load_state([1, 2, 3], Snapshot(state=0, covered=-1))
