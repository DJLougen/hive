"""Smoke tests: the public API happy path. Not the grading criterion."""

from __future__ import annotations

from paginate import RecordStore, decode_cursor, encode_cursor, page

ROWS = [
    {"id": "r1", "created_at": 10},
    {"id": "r2", "created_at": 20},
    {"id": "r3", "created_at": 30},
    {"id": "r4", "created_at": 40},
    {"id": "r5", "created_at": 50},
]


def test_first_page_is_the_head_of_the_order():
    first = page(ROWS, size=2)
    assert [r["id"] for r in first["items"]] == ["r1", "r2"]
    assert first["next_cursor"] is not None


def test_walking_to_the_end_terminates():
    store = RecordStore(ROWS)
    assert [r["id"] for r in store.walk(size=2)] == ["r1", "r2", "r3", "r4", "r5"]


def test_pages_are_ordered_by_sort_key_then_id():
    assert [r["id"] for r in page(ROWS, size=5)["items"]] == ["r1", "r2", "r3", "r4", "r5"]


def test_cursor_round_trip_preserves_the_sort_key():
    key, _tie = decode_cursor(encode_cursor(30, "r3"))
    assert key == 30


def test_empty_input_yields_an_empty_page():
    assert page([], size=2) == {"items": [], "next_cursor": None}


# --- Spec coverage: the issue's stated requirements, not just the happy path.
# These fail on the shipped repo; the held-out oracle checks the edge cases.

TIED = [
    {"id": "a1", "created_at": 10}, {"id": "a2", "created_at": 10},
    {"id": "a3", "created_at": 10}, {"id": "b1", "created_at": 20},
    {"id": "b2", "created_at": 20},
]


def test_a_boundary_inside_a_tie_group_drops_no_rows():
    seen = [r["id"] for r in RecordStore(TIED).walk(size=2)]
    assert seen == ["a1", "a2", "a3", "b1", "b2"]


def test_every_page_size_walks_the_same_complete_sequence():
    want = ["a1", "a2", "a3", "b1", "b2"]
    for size in (1, 2, 3, 4):
        assert [r["id"] for r in RecordStore(TIED).walk(size=size)] == want, size


def test_a_cursor_round_trips_both_parts():
    assert decode_cursor(encode_cursor(20, "b1")) == (20, "b1")


def test_a_resume_starts_strictly_after_the_cursor_row():
    start = page(TIED, cursor=encode_cursor(10, "a2"), size=3)
    assert [r["id"] for r in start["items"]] == ["a3", "b1", "b2"]


def test_a_foreign_cursor_is_rejected():
    import pytest

    with pytest.raises(ValueError):
        decode_cursor("not-a-cursor")
