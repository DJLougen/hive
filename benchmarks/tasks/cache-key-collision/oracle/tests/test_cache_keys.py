"""Held-out grading tests for argument-keyed memoisation."""

from __future__ import annotations

from cachekeys import MemoStore, build_key, canonical, memoize


def test_keyword_order_does_not_change_the_key():
    assert build_key("f", (), {"a": 1, "b": 2}) == build_key("f", (), {"b": 2, "a": 1})


def test_nested_mapping_order_does_not_change_the_key():
    first = {"opts": {"retries": 2, "mode": "fast"}}
    second = {"opts": {"mode": "fast", "retries": 2}}
    assert build_key("f", (), first) == build_key("f", (), second)


def test_positional_order_still_matters():
    assert build_key("f", (1, 2), {}) != build_key("f", (2, 1), {})


def test_values_that_only_look_alike_do_not_collide():
    assert build_key("f", (), {"v": 1}) != build_key("f", (), {"v": "1"})
    assert build_key("f", (), {"v": 1}) != build_key("f", (), {"v": True})
    assert build_key("f", (1,), {}) != build_key("f", ("1",), {})
    assert canonical(1) != canonical("1")


def test_reordered_keywords_hit_the_same_cache_entry():
    calls = []

    @memoize(store=MemoStore())
    def fetch(user, limit):
        calls.append((user, limit))
        return f"{user}:{limit}"

    assert fetch(user="ada", limit=5) == fetch(limit=5, user="ada")
    assert calls == [("ada", 5)]


def test_positional_and_keyword_spellings_hit_the_same_entry():
    calls = []

    @memoize(store=MemoStore())
    def fetch(user, limit):
        calls.append((user, limit))
        return f"{user}:{limit}"

    assert fetch("ada", 5) == fetch(user="ada", limit=5)
    assert fetch("ada", limit=5) == fetch("ada", 5)
    assert calls == [("ada", 5)]


def test_a_defaulted_parameter_and_an_explicit_default_are_one_call():
    calls = []

    @memoize(store=MemoStore())
    def fetch(user, limit=3):
        calls.append((user, limit))
        return f"{user}:{limit}"

    assert fetch("ada") == fetch("ada", 3)
    assert calls == [("ada", 3)]


def test_different_values_still_compute_separately():
    store = MemoStore()

    @memoize(store=store)
    def fetch(user, limit=3):
        return f"{user}:{limit}"

    assert fetch("ada") != fetch("ada", limit=4)
    assert store.misses == 2


def test_unhashable_arguments_are_keyable():
    key = build_key("f", ([1, 2], {"a": [3]}), {})
    assert isinstance(key, str) and key
    assert canonical({"tags": ["x", "y"]}) == canonical({"tags": ["x", "y"]})
