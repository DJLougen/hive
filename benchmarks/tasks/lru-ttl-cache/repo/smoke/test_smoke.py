"""Smoke tests: the disclosed spec. Fails on the shipped repo."""

from __future__ import annotations

from lrucache import LruTtlCache


def test_a_set_then_get_returns_the_value():
    c = LruTtlCache(2, 60.0)
    c.set("a", 1, 100.0)
    assert c.get("a", 110.0) == 1


def test_an_expired_entry_returns_none():
    c = LruTtlCache(2, 60.0)
    c.set("a", 1, 100.0)
    assert c.get("a", 161.0) is None


def test_a_full_cache_evicts_the_least_recently_used():
    c = LruTtlCache(2, 60.0)
    c.set("a", 1, 100.0)
    c.set("b", 2, 101.0)
    c.get("a", 102.0)          # 'a' is now the most recently used
    c.set("c", 3, 103.0)       # must evict 'b', the LRU — not 'a'
    assert c.get("a", 104.0) == 1
    assert c.get("b", 104.0) is None


def test_a_get_refreshes_recency():
    c = LruTtlCache(2, 60.0)
    c.set("a", 1, 100.0)
    c.set("b", 2, 101.0)
    c.get("a", 102.0)          # touch 'a' so 'b' is the LRU
    c.set("c", 3, 103.0)
    assert c.get("a", 104.0) == 1  # 'a' survived because it was used


def test_a_set_on_an_existing_key_refreshes_expiry():
    c = LruTtlCache(2, 60.0)
    c.set("a", 1, 100.0)
    c.set("a", 2, 150.0)       # re-set: new value, new expiry
    assert c.get("a", 200.0) == 2
    assert c.get("a", 211.0) is None
