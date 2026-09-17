from intervals import merge_intervals


def test_overlapping_merged():
    assert merge_intervals([(1, 4), (2, 5)]) == [(1, 5)]


def test_touching_merged():
    assert merge_intervals([(1, 3), (3, 5)]) == [(1, 5)]


def test_disjoint_kept_separate():
    assert merge_intervals([(1, 2), (4, 6)]) == [(1, 2), (4, 6)]


def test_unsorted_input_sorted():
    assert merge_intervals([(5, 7), (1, 2), (2, 5)]) == [(1, 7)]


def test_nested_absorbed():
    assert merge_intervals([(1, 10), (3, 4)]) == [(1, 10)]


def test_empty():
    assert merge_intervals([]) == []
