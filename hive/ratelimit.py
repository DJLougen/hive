"""Token-bucket rate limiter for Hive enterprise deployments.

Prevents noisy tenants or runaway agents from overwhelming the stack.
Each tenant gets an independent bucket per operation (route, compress,
remember, recall).

Usage::

    from hive.ratelimit import RateLimiter

    limiter = RateLimiter(default_capacity=100, refill_rate=10.0)
    allowed = limiter.check("tenant_a", "route")
    remaining = limiter.get_remaining("tenant_a", "route")
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field



@dataclass
class TokenBucket:
    """Simple token bucket."""

    capacity: int
    refill_rate: float  # tokens per second
    _tokens: float = field(default=0.0, repr=False)
    _last_refill: float = field(default_factory=time.monotonic, repr=False)

    def __post_init__(self) -> None:
        if self._tokens == 0.0:
            self._tokens = float(self.capacity)

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
        self._last_refill = now

    def consume(self, n: int = 1) -> bool:
        """Try to consume ``n`` tokens. Returns True if allowed."""
        self._refill()
        if self._tokens >= n:
            self._tokens -= n
            return True
        return False

    def remaining(self) -> int:
        """Return current token count (floored to int)."""
        self._refill()
        return int(self._tokens)


class RateLimiter:
    """Per-tenant, per-operation token-bucket rate limiter."""

    def __init__(self, *, default_capacity: int = 100, refill_rate: float = 10.0) -> None:
        self.default_capacity = default_capacity
        self.refill_rate = refill_rate
        self._buckets: dict[tuple[str, str], TokenBucket] = {}

    def _bucket(self, tenant_id: str, operation: str) -> TokenBucket:
        key = (tenant_id, operation)
        if key not in self._buckets:
            self._buckets[key] = TokenBucket(
                capacity=self.default_capacity,
                refill_rate=self.refill_rate,
            )
            self._buckets[key]._tokens = float(self.default_capacity)
        return self._buckets[key]

    def check(self, tenant_id: str, operation: str) -> bool:
        """Return True if the request is within the rate limit."""
        return self._bucket(tenant_id, operation).consume(1)

    def get_remaining(self, tenant_id: str, operation: str) -> int:
        """Return remaining tokens for the tenant/operation pair."""
        return self._bucket(tenant_id, operation).remaining()

    def reset(self, tenant_id: str) -> None:
        """Clear all buckets for a tenant (useful in tests)."""
        for key in list(self._buckets):
            if key[0] == tenant_id:
                del self._buckets[key]


__all__ = ["TokenBucket", "RateLimiter"]
