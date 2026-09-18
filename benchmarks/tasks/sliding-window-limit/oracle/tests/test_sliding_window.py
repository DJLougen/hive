"""Held-out grading tests for the sliding-window limiter."""

from __future__ import annotations

import pytest

from ratelimit import RateLimiter, RateLimitExceeded


def test_the_window_slides_with_the_callers_own_history():
    rl = RateLimiter(3, 60.0)
    rl.call("k", 10.5)
    rl.call("k", 50.0)
    rl.call("k", 69.9)
    # All three landed inside the last 60s — the fourth must be denied even
    # though a fixed window would have reset at t=60.
    with pytest.raises(RateLimitExceeded):
        rl.call("k", 70.0)


def test_the_window_is_left_open_at_exactly_w():
    rl = RateLimiter(2, 60.0)
    rl.call("k", 100.0)
    rl.call("k", 100.0)
    # t=160.0 is exactly W after the first two: they no longer count.
    rl.call("k", 160.0)
    rl.call("k", 160.0)
    with pytest.raises(RateLimitExceeded):
        rl.call("k", 160.0)


def test_a_call_just_inside_the_window_still_counts():
    rl = RateLimiter(2, 60.0)
    rl.call("k", 100.0)
    rl.call("k", 100.0)
    with pytest.raises(RateLimitExceeded):
        rl.call("k", 159.999)


def test_check_does_not_spend():
    rl = RateLimiter(1, 60.0)
    assert rl.check("k", 100.0)
    assert rl.check("k", 100.0)  # still true — check is not a call
    rl.call("k", 100.0)
    assert not rl.check("k", 100.0)


def test_retry_after_names_the_oldest_calls_expiry():
    rl = RateLimiter(2, 60.0)
    rl.call("k", 100.0)
    rl.call("k", 110.0)
    with pytest.raises(RateLimitExceeded) as err:
        rl.call("k", 120.0)
    # The oldest call (t=100) leaves the window at t=160, so retry is 40s.
    assert "40" in str(err.value)


def test_denied_calls_do_not_count_against_the_cap():
    rl = RateLimiter(1, 60.0)
    rl.call("k", 100.0)
    with pytest.raises(RateLimitExceeded):
        rl.call("k", 101.0)
    # The denied call at 101 must not extend the lockout: at t=160 the
    # original call expires and a new one is free.
    rl.call("k", 160.0)


def test_expired_timestamps_are_evicted_per_key():
    rl = RateLimiter(1, 60.0)
    rl.call("old", 0.0)
    rl.call("new", 1000.0)
    # 'old' has no calls inside [940, 1000) — it must not linger.
    assert rl.active_keys() == 1


def test_many_keys_do_not_share_a_window():
    rl = RateLimiter(1, 60.0)
    for i in range(50):
        rl.call(f"key-{i}", 100.0)
    for i in range(50):
        with pytest.raises(RateLimitExceeded):
            rl.call(f"key-{i}", 100.0)
