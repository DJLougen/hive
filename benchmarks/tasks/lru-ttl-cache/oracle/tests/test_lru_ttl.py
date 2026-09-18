"""Held-out grading tests for TTL + LRU interaction."""

from __future__ import annotations

from lrucache import LruTtlCache


def test_the_deadline_is_set_plus_ttl_inclusive():
    c = LruTtlCache(2, 60.0)
    c.set("a", 1, 100.0)
    assert c.get("a", 160.0) == 1      # boundary: still live
    assert c.get("a", 160.001) is None


def test_an_expired_entry_is_dropped_not_just_hidden():
    c = LruTtlCache(2, 60.0)
    c.set("a", 1, 100.0)
    c.get("a", 200.0)                  # expired — must be removed
    assert len(c) == 0


def test_a_get_does_not_extend_the_ttl():
    c = LruTtlCache(2, 60.0)
    c.set("a", 1, 100.0)
    c.get("a", 150.0)                  # use refreshes recency, NOT expiry
    assert c.get("a", 161.0) is None   # still dies at set+ttl


def test_recency_is_by_last_use_not_last_set():
    c = LruTtlCache(3, 60.0)
    c.set("a", 1, 100.0)
    c.set("b", 2, 101.0)
    c.set("c", 3, 102.0)
    c.get("a", 103.0)                  # 'a' most recently used
    c.get("b", 104.0)                  # 'b' next
    c.set("d", 4, 105.0)               # 'c' is LRU — evicted
    assert c.get("c", 106.0) is None
    assert c.get("a", 106.0) == 1 and c.get("b", 106.0) == 2


def test_a_set_on_an_existing_key_counts_as_a_use():
    c = LruTtlCache(2, 60.0)
    c.set("a", 1, 100.0)
    c.set("b", 2, 101.0)
    c.set("a", 9, 102.0)               # re-set 'a': now 'b' is LRU
    c.set("c", 3, 103.0)
    assert c.get("a", 104.0) == 9
    assert c.get("b", 104.0) is None


def test_expired_entries_free_capacity():
    c = LruTtlCache(2, 60.0)
    c.set("a", 1, 100.0)
    c.set("b", 2, 101.0)
    # Both expire; a new set must not evict a live entry that isn't there.
    c.set("c", 3, 200.0)
    assert len(c) <= 2


def test_eviction_prefers_an_expired_entry_over_a_live_lru():
    c = LruTtlCache(2, 60.0)
    c.set("a", 1, 100.0)
    c.set("b", 2, 150.0)
    # At t=200 'a' is expired, 'b' is live. A new set must drop 'a'.
    c.set("c", 3, 200.0)
    assert c.get("b", 201.0) == 2
    assert c.get("a", 201.0) is None


def test_len_counts_only_live_entries():
    c = LruTtlCache(3, 60.0)
    c.set("a", 1, 100.0)
    c.set("b", 2, 101.0)
    c.get("a", 200.0)                  # 'a' expires on access
    assert len(c) == 1


def test_a_missing_key_get_is_none():
    c = LruTtlCache(2, 60.0)
    assert c.get("nope", 100.0) is None


def test_interleaved_expiry_and_recency():
    c = LruTtlCache(2, 60.0)
    c.set("a", 1, 100.0)
    c.set("b", 2, 130.0)
    c.get("a", 140.0)                  # 'a' still live (set at 100, ttl 60)
    c.set("c", 3, 170.0)               # 'a' expired at 160 — evict it, not 'b'
    assert c.get("b", 171.0) == 2
    assert c.get("a", 171.0) is None
