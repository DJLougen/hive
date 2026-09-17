"""Held-out grading tests: paging must visit every row exactly once."""

from __future__ import annotations

from paginate import RecordStore, decode_cursor, encode_cursor, page

# Three rows share each sort key, so every page boundary lands inside a group.
TIED = [{"id": f"r{i:02d}", "created_at": t}
        for t in (10, 20, 30, 40)
        for i in range(3 * (t // 10 - 1), 3 * (t // 10 - 1) + 3)]


def _ids(rows):
    return [r["id"] for r in rows]


def test_first_page_starts_at_the_head():
    assert _ids(page(TIED, size=4)["items"]) == ["r00", "r01", "r02", "r03"]


def test_walking_visits_every_row_exactly_once():
    seen = _ids(RecordStore(TIED).walk(size=4))
    assert seen == _ids(sorted(TIED, key=lambda r: (r["created_at"], r["id"])))
    assert len(seen) == len(set(seen)) == len(TIED)


def test_a_page_boundary_inside_a_tie_group_does_not_repeat_the_row():
    first = page(TIED, size=4)
    second = page(TIED, cursor=first["next_cursor"], size=4)
    assert set(_ids(first["items"])) & set(_ids(second["items"])) == set()
    assert _ids(second["items"])[0] == "r04"


def test_resuming_from_a_hand_built_cursor_skips_only_that_row():
    start = page(TIED, cursor=encode_cursor(20, "r04"), size=3)
    assert _ids(start["items"]) == ["r05", "r06", "r07"]


def test_the_last_page_has_no_cursor():
    tail = page(TIED, cursor=encode_cursor(40, "r11"), size=5)
    assert tail["items"] == [] and tail["next_cursor"] is None
    full = page(TIED, size=len(TIED))
    assert full["next_cursor"] is None


def test_walking_every_tie_group_size_is_complete():
    for size in (1, 2, 3, 5, 7):
        seen = _ids(RecordStore(TIED).walk(size=size))
        assert seen == _ids(sorted(TIED, key=lambda r: (r["created_at"], r["id"]))), size


def test_cursor_round_trips_both_parts():
    assert decode_cursor(encode_cursor(20, "r04")) == (20, "r04")
