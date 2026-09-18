"""Smoke tests: the disclosed spec. Fails on the shipped repo."""

from __future__ import annotations

from kway import merge_streams


def test_two_sorted_streams_merge_in_order():
    out = list(merge_streams([[("a", 1), ("c", 3)], [("b", 2)]]))
    assert out == [("a", 1), ("b", 2), ("c", 3)]


def test_a_key_in_two_streams_yields_only_the_first():
    out = list(merge_streams([[("a", 1), ("b", 2)], [("b", 99), ("c", 3)]]))
    assert out == [("a", 1), ("b", 2), ("c", 3)]  # ("b", 99) dropped


def test_a_key_repeated_inside_one_stream_is_a_duplicate():
    out = list(merge_streams([[("a", 1), ("a", 2), ("b", 3)]]))
    assert out == [("a", 1), ("b", 3)]


def test_empty_streams_are_fine():
    assert list(merge_streams([[], [("a", 1)], []])) == [("a", 1)]
    assert list(merge_streams([])) == []


def test_the_merge_is_lazy():
    # A merge that materializes its input reads this whole stream before
    # yielding; a lazy merge yields the first record after one read. The
    # stream is long enough that materializing is detectably wrong, and the
    # sentinel at the end turns an over-read into a failure not a hang.
    def long_stream():
        for i in range(1000):
            yield (f"k{i:04d}", i)
        raise AssertionError("merge read past the record it needed")

    it = merge_streams([long_stream()])
    assert next(it) == ("k0000", 0)
    assert next(it) == ("k0001", 1)
