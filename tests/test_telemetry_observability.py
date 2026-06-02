"""Tests for telemetry observability exports."""

from __future__ import annotations

import json
import tempfile

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

    with open(path, "r", encoding="utf-8") as fh:
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

    with open(path, "r", encoding="utf-8") as fh:
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
