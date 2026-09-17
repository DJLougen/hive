from dedup import dedup


def test_exact_duplicates_removed():
    assert dedup(["a", "b", "a"]) == ["a", "b"]


def test_case_insensitive_duplicates_removed():
    assert dedup(["Alice", "ALICE", "alice"]) == ["Alice"]


def test_first_spelling_preserved():
    assert dedup(["BOB", "bob", "CAROL"]) == ["BOB", "CAROL"]


def test_order_preserved():
    assert dedup(["c", "a", "b", "A"]) == ["c", "a", "b"]


def test_empty():
    assert dedup([]) == []
