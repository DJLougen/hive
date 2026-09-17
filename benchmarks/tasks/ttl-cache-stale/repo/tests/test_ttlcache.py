import time

from ttlcache import TTLCache


def test_get_returns_value_before_expiry():
    c = TTLCache(ttl=60.0)
    c.set("k", "v")
    assert c.get("k") == "v"


def test_get_returns_none_after_expiry():
    c = TTLCache(ttl=0.05)
    c.set("k", "v")
    time.sleep(0.08)
    assert c.get("k") is None


def test_expired_entry_is_evicted():
    c = TTLCache(ttl=0.05)
    c.set("k", "v")
    time.sleep(0.08)
    c.get("k")
    assert "k" not in c._store


def test_missing_key_returns_none():
    c = TTLCache(ttl=60.0)
    assert c.get("nope") is None


def test_overwrite_refreshes_value():
    c = TTLCache(ttl=60.0)
    c.set("k", "v1")
    c.set("k", "v2")
    assert c.get("k") == "v2"
