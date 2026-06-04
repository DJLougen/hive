"""Tests for distributed tracing."""

from __future__ import annotations

from hive.tracing import TraceContext, Span


def test_traceparent_format():
    t = TraceContext(trace_id="abc123", parent_id="def456")
    assert t.traceparent.startswith("00-abc123-def456-")


def test_from_header():
    t = TraceContext.from_header("00-abc123-def456-01")
    assert t.trace_id == "abc123"
    assert t.parent_id == "def456"


def test_child_context():
    t = TraceContext()
    child = t.child()
    assert child.trace_id == t.trace_id
    assert child.parent_id != t.parent_id


def test_span_duration():
    with Span("test", TraceContext()) as span:
        pass
    assert span.duration_ms >= 0.0
    assert span.to_dict()["name"] == "test"
