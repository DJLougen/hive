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
