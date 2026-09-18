"""Held-out grading tests for the lazy deduplicating merge."""

from __future__ import annotations

from kway import merge_streams


def test_first_occurrence_is_in_merge_order_not_stream_order():
    # ("b", 7) arrives before ("b", 2) in merge order because stream 0's b is
    # yielded first — the first *yielded* wins, not the smallest value.
    out = list(merge_streams([[("b", 7)], [("a", 1), ("b", 2)]]))
    assert out == [("a", 1), ("b", 7)]


def test_a_key_in_every_stream_yields_once():
    out = list(merge_streams([[("x", 1)], [("x", 2)], [("x", 3)]]))
    assert out == [("x", 1)]


def test_interleaved_duplicates_across_three_streams():
    out = list(merge_streams([
        [("a", 1), ("c", 30)],
        [("a", 10), ("b", 20)],
        [("b", 200), ("c", 300)],
    ]))
    assert out == [("a", 1), ("b", 20), ("c", 30)]


def test_the_merge_does_not_materialize_the_whole_stream():
    # A materializing merge reads every record up front; a lazy merge reads a
    # stream only as its records approach the yield point. This stream counts
    # reads: a correct merge pulls at most one record ahead of what it has
    # yielded, never the whole stream at once.
    reads = []

    def counted():
        for rec in [("a", 1), ("b", 2), ("c", 3), ("d", 4)]:
            reads.append(rec)
            yield rec

    it = merge_streams([counted(), [("e", 5)]])
    assert next(it) == ("a", 1)
    # After one yield the merge may hold one pending record per stream — at
    # most 2 reads of the counted stream, never all 4.
    assert len(reads) <= 2
    assert list(it) == [("b", 2), ("c", 3), ("d", 4), ("e", 5)]


def test_all_same_key_collapses_to_one():
    assert list(merge_streams([[("k", 1), ("k", 2)], [("k", 3)]])) == [("k", 1)]


def test_a_single_stream_is_its_own_dedup():
    out = list(merge_streams([[("a", 1), ("a", 2), ("a", 3), ("b", 4)]]))
    assert out == [("a", 1), ("b", 4)]


def test_output_is_sorted_by_key():
    out = list(merge_streams([[("m", 1), ("z", 2)], [("a", 3), ("y", 4)]]))
    assert [k for k, _ in out] == sorted(k for k, _ in out)


def test_values_are_not_deduped_only_keys():
    # Same value under different keys is not a duplicate.
    out = list(merge_streams([[("a", 5)], [("b", 5)]]))
    assert out == [("a", 5), ("b", 5)]
