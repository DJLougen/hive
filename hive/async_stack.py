"""Async version of HiveStack for FastAPI and high-throughput deployments.

All hot-path operations are async so the event loop is not blocked on
compression, routing, or memory I/O. Thread-safe via `asyncio.Lock`.

Usage::

    from hive.async_stack import AsyncHiveStack

    stack = AsyncHiveStack()
    decision = await stack.route(state)
    compressed = await stack.compress("user", text)
"""

from __future__ import annotations

import asyncio
from typing import Any, Mapping, Sequence

from hive import HiveStack
from hive.config import HiveConfig
from hive.ratelimit import RateLimiter
from hive.telemetry import Telemetry
from hive.feedback import FeedbackBuffer
from hive.stack import CompressedTurn, RouteDecision
from hive.circuitbreaker import CircuitBreaker


class AsyncHiveStack:
    """Asyncio-compatible HiveStack.

    Wraps a synchronous HiveStack and delegates to a thread pool for
    CPU-bound work (compression, routing). Memory operations are
    lock-protected but fast enough to run inline.
    """

    def __init__(
        self,
        *,
        busybee_policy: Any | None = None,
        honey_comb: Any | None = None,
        rust_brain: Any | None = None,
        telemetry: Telemetry | None = None,
        feedback_buffer: FeedbackBuffer | None = None,
        tenant_id: str = "default",
        validate: bool = False,
        config: HiveConfig | None = None,
        rate_limiter: RateLimiter | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self._lock = asyncio.Lock()
        self._stack = HiveStack(
            busybee_policy=busybee_policy,
            honey_comb=honey_comb,
            rust_brain=rust_brain,
            telemetry=telemetry,
            feedback_buffer=feedback_buffer,
            tenant_id=tenant_id,
            validate=validate,
            config=config,
            rate_limiter=rate_limiter,
            circuit_breaker=circuit_breaker,
        )


    @property
    def stack(self) -> HiveStack:
        return self._stack

    async def route(self, state: Mapping[str, Any]) -> RouteDecision:
        async with self._lock:
            # CPU-bound: run in thread pool
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, self._stack.route, dict(state)
            )

    async def compress(self, role: str, content: str) -> CompressedTurn:
        async with self._lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, self._stack.compress, role, content
            )

    async def compress_many(
        self, turns: Sequence[tuple[str, str]]
    ) -> list[CompressedTurn]:
        # Parallel compression across messages
        return await asyncio.gather(
            *(self.compress(r, c) for r, c in turns)
        )

    async def remember(
        self, key: str, value: Any, *, trust: float = 1.0, tags: set[str] | None = None
    ) -> Any:
        async with self._lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, self._stack.remember, key, value,
            )

    async def recall(self, key: str, default: Any = None) -> Any:
        async with self._lock:
            return self._stack.recall(key, default)

    async def step(
        self, state: Mapping[str, Any], transcript: Sequence[tuple[str, str]]
    ) -> dict[str, Any]:
        decision = await self.route(state)
        compressed = await self.compress_many(transcript)
        return {
            "decision": decision,
            "compressed": compressed,
        }

    async def stats(self) -> dict[str, Any]:
        async with self._lock:
            return self._stack.stats()


__all__ = ["AsyncHiveStack"]
