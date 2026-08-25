"""Integration tests for telemetry observability exports."""

from __future__ import annotations

import json
import tempfile
import time
import urllib.request

import pytest

from hive import HiveStack
from hive.rule_fast import RuleFastHoneyComb
from hive.telemetry import Telemetry


def test_telemetry_jsonl_export():
    """Telemetry events can be exported to JSONL."""
    tel = Telemetry()
    stack = HiveStack(honey_comb=RuleFastHoneyComb(), telemetry=tel)
    stack.route({"goal": "test", "available_tools": []})
    stack.remember("key", "value")
    stack.recall("key")
    stack.compress("user", "hello world")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
        path = fh.name

    count = tel.export_jsonl(path)
    assert count >= 3  # routing + write + read (compress may or may not fire)

    with open(path, encoding="utf-8") as fh:
        lines = [json.loads(line) for line in fh]

    event_types = {line["event"] for line in lines}
    assert "routing" in event_types
    assert "memory_write" in event_types
    assert "memory_read" in event_types


def test_telemetry_jsonl_append_mode():
    """Real-time JSONL append records events as they happen."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
        path = fh.name

    tel = Telemetry()
    tel.enable_jsonl_append(path)
    stack = HiveStack(honey_comb=RuleFastHoneyComb(), telemetry=tel)
    stack.route({"goal": "test", "available_tools": []})

    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()

    assert len(lines) >= 1
    record = json.loads(lines[0])
    assert record["event"] == "routing"
    assert "latency_ms" in record


def test_telemetry_summary_shape():
    """Summary returns expected keys."""
    tel = Telemetry()
    stack = HiveStack(honey_comb=RuleFastHoneyComb(), telemetry=tel)
    stack.route({"goal": "test", "available_tools": []})
    stack.compress("user", "hello")

    summary = tel.summary()
    assert "routing" in summary
    assert "compression" in summary
    assert "memory_writes" in summary
    assert "memory_reads" in summary
    assert summary["routing"]["count"] >= 1
    assert summary["compression"]["count"] >= 1


def test_prometheus_server_metrics():
    """Start Prometheus server and verify metrics endpoint returns data."""
    pytest.importorskip("prometheus_client", reason="prometheus-client not installed")

    port = 9876
    tel = Telemetry()
    tel.start_prometheus_server(port=port)

    stack = HiveStack(honey_comb=RuleFastHoneyComb(), telemetry=tel)
    stack.route({"goal": "test", "available_tools": []})
    stack.compress("user", "hello")
    stack.remember("k", "v")
    stack.recall("k")

    # Give server time to start
    time.sleep(0.5)

    req = urllib.request.Request(f"http://127.0.0.1:{port}/metrics")
    with urllib.request.urlopen(req, timeout=2.0) as resp:
        body = resp.read().decode("utf-8")

    assert "hive_routing_total" in body
    assert "hive_compression_total" in body
    assert "hive_memory_writes_total" in body
    assert "hive_memory_reads_total" in body


def test_opentelemetry_traces_enabled():
    """Enable OpenTelemetry and verify tracer is configured."""
    pytest.importorskip("opentelemetry", reason="opentelemetry not installed")
    from opentelemetry import trace

    tel = Telemetry()
    tel.enable_otel_traces()

    assert tel._otel_enabled is True
    assert tel._otel_tracer is not None

    # Verify tracer provider was set
    provider = trace.get_tracer_provider()
    assert provider is not None

    # Try creating a span
    tracer = trace.get_tracer("hive.telemetry")
    with tracer.start_as_current_span("test_span") as span:
        span.set_attribute("test", True)
        assert span.is_recording()
