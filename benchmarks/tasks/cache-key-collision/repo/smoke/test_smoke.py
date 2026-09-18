"""Smoke tests: the public API happy path. Not the grading criterion."""

from __future__ import annotations

from cachekeys import MemoStore, build_key, canonical, memoize


def test_same_call_twice_computes_once():
    calls = []

    @memoize(store=MemoStore())
    def add(a, b):
        calls.append((a, b))
        return a + b

    assert add(1, 2) == add(1, 2) == 3
    assert calls == [(1, 2)]


def test_different_arguments_compute_separately():
    store = MemoStore()
    assert store.get_or_compute("a", lambda: 1) == 1
    assert store.get_or_compute("b", lambda: 2) == 2
    assert (store.hits, store.misses, len(store)) == (0, 2, 2)


def test_canonical_renders_containers_and_scalars():
    assert canonical([1, 2]) == canonical([1, 2])
    assert canonical({"a": 1}) == canonical({"a": 1})
    assert isinstance(canonical(3), str)


def test_key_names_the_function_and_its_arguments():
    key = build_key("add", (1, 2), {})
    assert key.startswith("add|")
    assert build_key("add", (1, 2), {}) == key
    assert build_key("add", (2, 1), {}) != key


# --- Spec coverage: the issue's stated requirements, not just the happy path.
# These fail on the shipped repo; the held-out oracle checks the edge cases.


def test_keyword_order_does_not_change_the_key():
    assert build_key("f", (), {"a": 1, "b": 2}) == build_key("f", (), {"b": 2, "a": 1})


def test_positional_and_keyword_spellings_hit_the_same_entry():
    calls = []

    @memoize(store=MemoStore())
    def fetch(user, limit):
        calls.append((user, limit))
        return f"{user}:{limit}"

    assert fetch("ada", 5) == fetch(user="ada", limit=5)
    assert calls == [("ada", 5)]


def test_a_defaulted_parameter_and_an_explicit_default_are_one_call():
    calls = []

    @memoize(store=MemoStore())
    def fetch(user, limit=3):
        calls.append((user, limit))
        return f"{user}:{limit}"

    assert fetch("ada") == fetch("ada", 3)
    assert calls == [("ada", 3)]


def test_values_that_only_look_alike_do_not_collide():
    assert build_key("f", (), {"v": 1}) != build_key("f", (), {"v": "1"})
    assert canonical(1) != canonical("1")
