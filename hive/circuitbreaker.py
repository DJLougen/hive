"""Circuit breaker for LLM and external service calls.

Prevents cascading failures when the LLM backend is down or slow.
Three states: CLOSED (normal), OPEN (failing fast), HALF_OPEN (testing).

Usage::

    from hive.circuitbreaker import CircuitBreaker

    cb = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)

    try:
        with cb:
            response = llm_client.call(prompt)
    except CircuitBreakerOpen:
        # LLM is down — return cached fallback or escalate
        return RouteDecision(tool="escalate", ...)
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum
from typing import Any


class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing fast
    HALF_OPEN = "half_open"  # Testing recovery


class CircuitBreakerOpen(Exception):
    """Raised when the circuit breaker is OPEN and a call is attempted."""

    pass


class CircuitBreaker:
    """Circuit breaker for external service calls.

    Parameters
    ----------
    failure_threshold:
        Number of consecutive failures before OPEN.
    recovery_timeout:
        Seconds to wait before HALF_OPEN.
    half_open_max_calls:
        Successful calls needed to close the circuit.
    expected_exception:
        Exception type that counts as a failure.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 3,
        expected_exception: type[BaseException] = Exception,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max_calls = half_open_max_calls
        self._expected_exception = expected_exception

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float | None = None
        self._lock = threading.RLock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state

    def _should_open(self) -> bool:
        return self._failure_count >= self._failure_threshold

    def _should_attempt_reset(self) -> bool:
        if self._last_failure_time is None:
            return False
        return time.monotonic() - self._last_failure_time >= self._recovery_timeout

    @contextmanager
    def call(self) -> Iterator[None]:
        """Context manager for wrapped calls."""
        self._before_call()
        try:
            yield
            self._on_success()
        except self._expected_exception:
            self._on_failure()
            raise

    def _before_call(self) -> None:
        with self._lock:
            if self._state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self._state = CircuitState.HALF_OPEN
                    self._success_count = 0
                else:
                    raise CircuitBreakerOpen(
                        f"Circuit breaker OPEN (failures={self._failure_count}, "
                        f"last_failure={self._last_failure_time})"
                    )

    def _on_success(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self._half_open_max_calls:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
            else:
                self._failure_count = 0

    def _on_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._state == CircuitState.HALF_OPEN or self._should_open():
                self._state = CircuitState.OPEN

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._state.value,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
                "failure_threshold": self._failure_threshold,
                "recovery_timeout": self._recovery_timeout,
            }
