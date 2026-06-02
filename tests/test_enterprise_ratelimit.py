"""Tests for enterprise rate limiting."""

from __future__ import annotations

import time

from hive.ratelimit import TokenBucket, RateLimiter
from hive import HiveStack
from hive.rule_fast import RuleFastHoneyComb


def test_token_bucket_allows_within_capacity():
    bucket = TokenBucket(capacity=5, refill_rate=1.0)
    assert bucket.consume()
    assert bucket.consume()
    assert bucket.remaining() == 3


def test_token_bucket_rejects_over_capacity():
    bucket = TokenBucket(capacity=2, refill_rate=1.0)
    assert bucket.consume()
    assert bucket.consume()
    assert not bucket.consume()  # Empty


def test_rate_limiter_per_tenant_isolation():
    limiter = RateLimiter(default_capacity=2, refill_rate=1.0)
    assert limiter.check("tenant_a", "route")
    assert limiter.check("tenant_a", "route")
    assert not limiter.check("tenant_a", "route")
    # Tenant b still has capacity
    assert limiter.check("tenant_b", "route")


def test_rate_limit_refills_over_time():
    limiter = RateLimiter(default_capacity=1, refill_rate=10.0)
    assert limiter.check("tenant", "route")
    assert not limiter.check("tenant", "route")
    time.sleep(0.15)  # Should refill ~1.5 tokens
    assert limiter.check("tenant", "route")


def test_hive_stack_with_rate_limiter():
    limiter = RateLimiter(default_capacity=1, refill_rate=0.1)
    stack = HiveStack(honey_comb=RuleFastHoneyComb(), rate_limiter=limiter)
    # First call succeeds
    d1 = stack.route({"goal": "test", "available_tools": []})
    assert d1.source != "ratelimit"
    # Second call is rate limited
    d2 = stack.route({"goal": "test", "available_tools": []})
    assert d2.source == "ratelimit"
    assert d2.tool == "escalate"
