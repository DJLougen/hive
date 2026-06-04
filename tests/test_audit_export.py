"""Tests for SIEM audit export formatters."""

from __future__ import annotations

import json

from hive.audit_export import AuditExporter


def test_to_splunk_format():
    exporter = AuditExporter(source="hive")
    events = [{"ts": 1704067200, "action": "route", "tool": "read_file"}]
    output = exporter.to_splunk(events)
    parsed = json.loads(output.strip().split("\n")[0])
    assert parsed["source"] == "hive"
    assert parsed["sourcetype"] == "_json"
    assert parsed["event"]["action"] == "route"


def test_to_elasticsearch_format():
    exporter = AuditExporter()
    events = [{"id": "ev1", "action": "compress", "label": "distill"}]
    output = exporter.to_elasticsearch(events)
    lines = output.strip().split("\n")
    assert json.loads(lines[0])["index"]["_index"] == "hive-audit"


def test_to_cloudwatch_format():
    exporter = AuditExporter()
    events = [{"ts": 1704067200, "action": "remember"}]
    output = exporter.to_cloudwatch(events)
    parsed = json.loads(output)
    assert len(parsed) == 1
    assert "timestamp" in parsed[0]
    assert "message" in parsed[0]
