"""Observability collector for Hive stack.

Records structured events during routing, compression, and memory
operations. Designed to be *optional* — passing ``telemetry=None`` to
``HiveStack`` skips all recording (no overhead beyond a None check).

The collector intentionally stores raw event lists rather than
pre-aggregated counters so callers can slice events by any attribute
(confidence buckets, latency percentiles, label distributions) without
the collector hard-coding what to measure.

Thread-safety: the collector is **not** thread-safe. One collector per
stack, one stack per thread. If you run Hive across threads, give each
thread its own ``HiveStack(telemetry=Telemetry())`` and merge afterwards.

Usage::

    from hive import HiveStack
    from hive.telemetry import Telemetry

    tel = Telemetry()
    stack = HiveStack(telemetry=tel)
    stack.route(state)
    stack.compress("user", "hello")
    print(tel.summary())

Export options (all optional)::

    # JSONL — one JSON object per line, append-only
    tel.export_jsonl("hive_events.jsonl")

    # Prometheus metrics endpoint (requires prometheus-client)
    tel.start_prometheus_server(port=9090)

    # OpenTelemetry spans (requires opentelemetry-api)
    tel.enable_otel_traces()
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

_log = logging.getLogger("hive.telemetry")


@dataclass(slots=True)
class RoutingEvent:
    """One routing decision."""

    source: str  # "busybee" | "fallback"
    action: str  # tool name or "escalate"
    confidence: float  # 0.0 – 1.0
    latency_ms: float  # ms to decide
    escalated: bool  # True if action == "escalate" or source == "fallback"


@dataclass(slots=True)
class CompressionEvent:
    """One compression pass."""

    role: str  # "user" | "assistant" | "system" | "tool"
    label: str  # "core" | "drop" | "compact" | "distill" | ...
    original_tokens: int
    compressed_tokens: int
    latency_ms: float


@dataclass(slots=True)
class MemoryWriteEvent:
    """One memory node write."""

    key: str
    trust: float
    has_causal_edge: bool
    has_tags: bool
    latency_ms: float


@dataclass(slots=True)
class MemoryReadEvent:
    """One memory recall."""

    key: str
    hit: bool  # True if the key existed
    latency_ms: float


@dataclass
class Telemetry:
    """Collects routing, compression, and memory events.

    All four lists start empty; callers append via the ``record_*`` helpers
    which HiveStack calls internally. Direct list access is fine for callers
    that want to drain or slice.
    """

    routing: list[RoutingEvent] = field(default_factory=list)
    compression: list[CompressionEvent] = field(default_factory=list)
    memory_writes: list[MemoryWriteEvent] = field(default_factory=list)
    memory_reads: list[MemoryReadEvent] = field(default_factory=list)

    max_events: int = 10_000

    # -- export configuration -----------------------------------------------

    jsonl_path: str | None = None
    _otel_enabled: bool = field(default=False, repr=False)
    _prom_enabled: bool = field(default=False, repr=False)

    # -- internal helpers -------------------------------------------------

    def _maybe_trim(self, lst: list) -> None:
        """Trim list to max_events, keeping newest (right side)."""
        excess = len(lst) - self.max_events
        if excess > 0:
            del lst[:excess]

    def _emit(self, record: dict[str, Any]) -> None:
        """Write a single record to configured exporters."""
        if self.jsonl_path:
            try:
                with open(self.jsonl_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, default=str) + "\n")
            except OSError as exc:
                _log.warning("jsonl export failed: %s", exc)

    # -- recording helpers (called by HiveStack) --------------------------

    def record_routing(
        self,
        *,
        source: str,
        action: str,
        confidence: float,
        latency_ms: float,
        escalated: bool,
    ) -> None:
        ev = RoutingEvent(
            source=source,
            action=action,
            confidence=confidence,
            latency_ms=latency_ms,
            escalated=escalated,
        )
        self.routing.append(ev)
        self._maybe_trim(self.routing)
        self._emit({"event": "routing", **asdict(ev)})

    def record_compression(
        self,
        *,
        role: str,
        label: str,
        original_tokens: int,
        compressed_tokens: int,
        latency_ms: float,
    ) -> None:
        ev = CompressionEvent(
            role=role,
            label=label,
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            latency_ms=latency_ms,
        )
        self.compression.append(ev)
        self._maybe_trim(self.compression)
        self._emit({"event": "compression", **asdict(ev)})

    def record_memory_write(
        self,
        *,
        key: str,
        trust: float,
        has_causal_edge: bool,
        has_tags: bool,
        latency_ms: float,
    ) -> None:
        ev = MemoryWriteEvent(
            key=key,
            trust=trust,
            has_causal_edge=has_causal_edge,
            has_tags=has_tags,
            latency_ms=latency_ms,
        )
        self.memory_writes.append(ev)
        self._maybe_trim(self.memory_writes)
        self._emit({"event": "memory_write", **asdict(ev)})

    def record_memory_read(
        self,
        *,
        key: str,
        hit: bool,
        latency_ms: float,
    ) -> None:
        ev = MemoryReadEvent(key=key, hit=hit, latency_ms=latency_ms)
        self.memory_reads.append(ev)
        self._maybe_trim(self.memory_reads)
        self._emit({"event": "memory_read", **asdict(ev)})

    # -- aggregation (caller-facing) --------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return an aggregated view suitable for JSON / print."""

        def latency_stats(events: list[Any]) -> dict[str, float]:
            if not events:
                return {"count": 0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
            times = sorted(e.latency_ms for e in events)
            n = len(times)
            p50 = times[n // 2]
            p95 = times[int(n * 0.95)] if n >= 20 else times[-1]
            return {
                "count": n,
                "p50_ms": round(p50, 4),
                "p95_ms": round(p95, 4),
                "max_ms": round(times[-1], 4),
            }

        escalated = sum(1 for e in self.routing if e.escalated)
        routing_count = len(self.routing)

        tokens_in = sum(e.original_tokens for e in self.compression)
        tokens_out = sum(e.compressed_tokens for e in self.compression)

        read_hits = sum(1 for e in self.memory_reads if e.hit)
        read_count = len(self.memory_reads)

        return {
            "routing": {
                **latency_stats(self.routing),
                "busybee_pct": round(
                    sum(1 for e in self.routing if e.source == "busybee")
                    / max(routing_count, 1)
                    * 100,
                    2,
                ),
                "escalated_count": escalated,
                "escalated_pct": round(
                    escalated / max(routing_count, 1) * 100, 2
                ),
            },
            "compression": {
                **latency_stats(self.compression),
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "ratio": round(tokens_in / max(tokens_out, 1), 3),
            },
            "memory_writes": {
                **latency_stats(self.memory_writes),
                "with_causal_edge": sum(
                    1 for e in self.memory_writes if e.has_causal_edge
                ),
            },
            "memory_reads": {
                **latency_stats(self.memory_reads),
                "hit_rate_pct": round(
                    read_hits / max(read_count, 1) * 100, 2
                ),
            },
        }

    def clear(self) -> None:
        """Drop all recorded events."""
        self.routing.clear()
        self.compression.clear()
        self.memory_writes.clear()
        self.memory_reads.clear()

    # -- export helpers ---------------------------------------------------

    def export_jsonl(self, path: str) -> int:
        """Flush all accumulated events to a JSONL file and return count written."""
        events: list[dict[str, Any]] = []
        events += [{"event": "routing", **asdict(ev)} for ev in self.routing]
        events += [{"event": "compression", **asdict(ev)} for ev in self.compression]
        events += [{"event": "memory_write", **asdict(ev)} for ev in self.memory_writes]
        events += [{"event": "memory_read", **asdict(ev)} for ev in self.memory_reads]

        if not events:
            return 0

        with open(path, "w", encoding="utf-8") as fh:
            for record in events:
                fh.write(json.dumps(record, default=str) + "\n")

        _log.info("Exported %d events to %s", len(events), path)
        return len(events)

    def enable_jsonl_append(self, path: str) -> None:
        """Enable real-time JSONL append mode. Every recorded event is appended immediately."""
        self.jsonl_path = path
        _log.info("JSONL append enabled: %s", path)

    def start_prometheus_server(self, port: int = 9090) -> None:
        """Start a Prometheus metrics HTTP endpoint (blocking; run in thread)."""
        try:
            from prometheus_client import start_http_server, Gauge, Counter, Histogram

            self._prom = {
                "routing_total": Counter("hive_routing_total", "Routing decisions", ["source", "action"]),
                "routing_latency": Histogram("hive_routing_latency_ms", "Routing latency"),
                "compression_total": Counter("hive_compression_total", "Compression runs", ["label"]),
                "compression_ratio": Gauge("hive_compression_ratio", "Current compression ratio"),
                "memory_writes_total": Counter("hive_memory_writes_total", "Memory writes"),
                "memory_reads_total": Counter("hive_memory_reads_total", "Memory reads", ["hit"]),
                "memory_read_latency": Histogram("hive_memory_read_latency_ms", "Memory read latency"),
            }
            start_http_server(port)
            self._prom_enabled = True
            _log.info("Prometheus metrics on http://localhost:%d/metrics", port)
        except ImportError:
            _log.warning("prometheus-client not installed; run: pip install prometheus-client")

    def enable_otel_traces(self) -> None:
        """Enable OpenTelemetry span creation for every recorded event."""
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.resources import Resource

            resource = Resource.create({"service.name": "hive"})
            provider = TracerProvider(resource=resource)
            trace.set_tracer_provider(provider)
            self._otel_tracer = trace.get_tracer("hive.telemetry")
            self._otel_enabled = True
            _log.info("OpenTelemetry traces enabled")
        except ImportError:
            _log.warning("opentelemetry-sdk not installed; run: pip install opentelemetry-api opentelemetry-sdk")
