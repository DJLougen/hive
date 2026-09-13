"""Distributed tracing support via W3C traceparent.

Propagates trace context through route → compress → llm → remember so
Jaeger/Zipkin shows the full agent turn end-to-end.

Usage::

    from hive.tracing import TraceContext

    trace = TraceContext.start()
    decision = stack.route(state, traceparent=trace.traceparent)
    compressed = stack.compress("user", text, traceparent=trace.traceparent)
    # All telemetry spans share the same trace ID
"""

from __future__ import annotations

import secrets
import time
from typing import Any


def _generate_id() -> str:
    """Return a 16-byte hex string."""
    return secrets.token_hex(16)


class TraceContext:
    """W3C traceparent context holder."""

    def __init__(
        self,
        *,
        trace_id: str | None = None,
        parent_id: str | None = None,
        trace_flags: str = "01",
    ) -> None:
        self.trace_id = trace_id or _generate_id()
        self.parent_id = parent_id or _generate_id()
        self.trace_flags = trace_flags

    @property
    def traceparent(self) -> str:
        """Return the W3C traceparent header value."""
        return f"00-{self.trace_id}-{self.parent_id}-{self.trace_flags}"

    @classmethod
    def from_header(cls, header: str | None) -> TraceContext:
        if not header:
            return cls()
        parts = header.split("-")
        if len(parts) != 4:
            return cls()
        return cls(
            trace_id=parts[1],
            parent_id=parts[2],
            trace_flags=parts[3],
        )

    def child(self) -> TraceContext:
        """Create a child span context."""
        return TraceContext(
            trace_id=self.trace_id,
            parent_id=_generate_id(),
            trace_flags=self.trace_flags,
        )


class Span:
    """Manual span helper (lightweight, no OTel dependency)."""

    def __init__(self, name: str, context: TraceContext) -> None:
        self.name = name
        self.context = context
        self.started: float | None = None
        self.ended: float | None = None

    def __enter__(self) -> Span:
        self.started = time.perf_counter()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.ended = time.perf_counter()

    @property
    def duration_ms(self) -> float:
        if self.started is None or self.ended is None:
            return 0.0
        return (self.ended - self.started) * 1000.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "trace_id": self.context.trace_id,
            "span_id": self.context.parent_id,
            "duration_ms": round(self.duration_ms, 3),
            "start_time": self.started,
            "end_time": self.ended,
        }


__all__ = ["Span", "TraceContext"]
