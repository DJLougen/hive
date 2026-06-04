"""Tests for the circuit breaker module."""

from __future__ import annotations

import time

import pytest

from hive.circuitbreaker import CircuitBreaker, CircuitBreakerOpen, CircuitState


def test_circuit_starts_closed():
    cb = CircuitBreaker()
    assert cb.state == CircuitState.CLOSED


def test_success_keeps_circuit_closed():
    cb = CircuitBreaker()
    with cb.call():
        pass
    assert cb.state == CircuitState.CLOSED
    assert cb.stats()["failure_count"] == 0


def test_failure_increments_count():
    cb = CircuitBreaker(failure_threshold=3)
    for _ in range(2):
        with pytest.raises(ValueError):
            with cb.call():
                raise ValueError("boom")
    assert cb.state == CircuitState.CLOSED
    assert cb.stats()["failure_count"] == 2


def test_threshold_opens_circuit():
    cb = CircuitBreaker(failure_threshold=3)
    for _ in range(3):
        with pytest.raises(ValueError):
            with cb.call():
                raise ValueError("boom")
    assert cb.state == CircuitState.OPEN


def test_open_circuit_raises():
    cb = CircuitBreaker(failure_threshold=1)
    with pytest.raises(ValueError):
        with cb.call():
            raise ValueError("boom")
    with pytest.raises(CircuitBreakerOpen):
        with cb.call():
            pass


def test_recovery_timeout_half_open():
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)
    with pytest.raises(ValueError):
        with cb.call():
            raise ValueError("boom")
    assert cb.state == CircuitState.OPEN
    time.sleep(0.15)
    # After recovery timeout, circuit transitions to HALF_OPEN
    # and allows the next call through
    with cb.call():
        pass
    assert cb.state == CircuitState.HALF_OPEN


def test_half_open_closes_after_successes():
    cb = CircuitBreaker(
        failure_threshold=1,
        recovery_timeout=0.1,
        half_open_max_calls=2,
    )
    with pytest.raises(ValueError):
        with cb.call():
            raise ValueError("boom")
    time.sleep(0.15)
    with cb.call():
        pass
    assert cb.state == CircuitState.HALF_OPEN
    time.sleep(0.15)
    with cb.call():
        pass
    assert cb.state == CircuitState.CLOSED


def test_stats_shape():
    cb = CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)
    stats = cb.stats()
    assert stats["state"] == "closed"
    assert stats["failure_threshold"] == 5
    assert stats["recovery_timeout"] == 60.0
