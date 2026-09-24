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
import functools
from collections.abc import Mapping, Sequence
from typing import Any

from hive import HiveStack
from hive.circuitbreaker import CircuitBreaker
from hive.config import HiveConfig
from hive.feedback import FeedbackBuffer
from hive.ratelimit import RateLimiter
from hive.stack import CompressedTurn, RouteDecision
from hive.telemetry import Telemetry


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
        gossip: Any | None = None,
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
            gossip=gossip,
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
        # Delegate to the sync batch path so total max_content_bytes is
        # enforced (parallel per-message compress() would bypass the cap).
        async with self._lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None,
                functools.partial(self._stack.compress_many, turns),
            )

    async def remember(
        self,
        key: str,
        value: Any,
        *,
        trust: float = 1.0,
        tags: Sequence[str] | None = None,
        caused_by: Sequence[str] | None = None,
    ) -> Any:
        async with self._lock:
            loop = asyncio.get_running_loop()
            # functools.partial so the keyword-only args actually reach
            # HiveStack.remember — run_in_executor forwards positionals only.
            return await loop.run_in_executor(
                None,
                functools.partial(
                    self._stack.remember,
                    key,
                    value,
                    trust=trust,
                    tags=tags,
                    caused_by=caused_by,
                ),
            )

    async def recall(self, key: str, default: Any = None) -> Any:
        async with self._lock:
            return self._stack.recall(key, default)

    async def step(
        self, state: Mapping[str, Any], transcript: Sequence[tuple[str, str]]
    ) -> dict[str, Any]:
        # Delegate the whole step so the result matches HiveStack.step()
        # exactly: last-turn compression, decision persisted to the brain,
        # and a stats payload.
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            functools.partial(self._stack.step, dict(state), transcript),
        )

    async def stats(self) -> dict[str, Any]:
        async with self._lock:
            return self._stack.stats()


__all__ = ["AsyncHiveStack"]
