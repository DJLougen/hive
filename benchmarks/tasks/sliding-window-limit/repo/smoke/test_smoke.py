"""Smoke tests: the disclosed spec. Fails on the shipped repo."""

from __future__ import annotations

import pytest

from ratelimit import RateLimiter, RateLimitExceeded


def test_calls_up_to_the_limit_are_allowed():
    rl = RateLimiter(3, 60.0)
    for _ in range(3):
        rl.call("k", 100.0)


def test_the_call_past_the_limit_is_denied():
    rl = RateLimiter(2, 60.0)
    rl.call("k", 100.0)
    rl.call("k", 100.0)
    with pytest.raises(RateLimitExceeded):
        rl.call("k", 100.0)


def test_calls_in_a_sliding_window_count_together():
    # The bug: 2 calls at the end of one fixed window + 2 at the start of the
    # next must not all pass — they are 4 calls inside 60 sliding seconds.
    rl = RateLimiter(3, 60.0)
    rl.call("k", 59.0)
    rl.call("k", 59.5)
    rl.call("k", 60.0)
    with pytest.raises(RateLimitExceeded):
        rl.call("k", 60.5)


def test_a_call_exactly_one_window_later_is_free():
    # [now-W, now) is left-open: a call at t+W does not count the call at t.
    rl = RateLimiter(2, 60.0)
    rl.call("k", 100.0)
    rl.call("k", 100.0)
    rl.call("k", 160.0)  # the two at t=100 are exactly W behind — not counted


def test_keys_are_independent():
    rl = RateLimiter(1, 60.0)
    rl.call("a", 100.0)
    rl.call("b", 100.0)  # different key, different budget
    with pytest.raises(RateLimitExceeded):
        rl.call("a", 100.0)


def test_quiet_keys_do_not_leak_memory():
    rl = RateLimiter(1, 60.0)
    rl.call("gone", 0.0)
    rl.call("gone", 61.0)  # first call expired out of the window
    assert rl.active_keys() <= 1
