"""Tests for enterprise data retention / TTL."""

from __future__ import annotations

import time

from hive.rust_brain import RustBrain


def test_ttl_expires_old_entries():
    brain = RustBrain(default_ttl_s=0.1)
    brain.remember("key", "value")
    assert brain.recall("key") == "value"
    time.sleep(0.15)
    assert brain.expire("key") is True
    assert brain.recall("key") is None


def test_gc_expired_removes_multiple():
    brain = RustBrain(default_ttl_s=0.05)
    brain.remember("a", 1)
    brain.remember("b", 2)
    time.sleep(0.1)
    removed = brain.gc_expired()
    assert removed == 2
    assert brain.recall("a") is None
    assert brain.recall("b") is None


def test_no_ttl_preserves_all():
    brain = RustBrain()  # No TTL
    brain.remember("key", "value")
    time.sleep(0.05)
    assert brain.expire("key") is False
    assert brain.recall("key") == "value"


def test_stack_with_ttl():
    from hive import HiveStack
    from hive.rule_fast import RuleFastHoneyComb

    stack = HiveStack(honey_comb=RuleFastHoneyComb(), tenant_id="ttl_test")
    stack.remember("temp", "data")
    # Stack doesn't force TTL by default, but brain supports it
    assert stack.recall("temp") == "data"
