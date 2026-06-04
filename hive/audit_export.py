"""SIEM-compatible audit log exporters.

Formats audit logs for Splunk, Elasticsearch, and CloudWatch so SOC 2
auditors can consume them in existing tools.

Usage::

    from hive.audit_export import AuditExporter

    exporter = AuditExporter(source="hive-agent-memory")
    exporter.to_splunk(audit_events, "/var/log/hive/splunk.json")
    exporter.to_elasticsearch(audit_events, "http://localhost:9200/hive-audit/_bulk")
"""

from __future__ import annotations

import json
import time
from typing import Any


class AuditExporter:
    """Export Hive audit events to SIEM formats."""

    def __init__(self, *, source: str = "hive-agent-memory", host: str = "localhost") -> None:
        self._source = source
        self._host = host

    def to_splunk(self, events: list[dict[str, Any]], path: str | None = None) -> str:
        """Export to Splunk HEC (HTTP Event Collector) JSON format.

        One JSON object per line:
        {"time": 1704067200, "event": {...}, "source": "hive", "sourcetype": "_json"}
        """
        lines: list[str] = []
        for ev in events:
            payload = {
                "time": ev.get("ts", time.time()),
                "event": ev,
                "source": self._source,
                "sourcetype": "_json",
                "host": self._host,
            }
            lines.append(json.dumps(payload, ensure_ascii=False))

        output = "\n".join(lines)
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(output + "\n")
        return output

    def to_elasticsearch(
        self, events: list[dict[str, Any]], *, path: str | None = None
    ) -> str:
        """Export to Elasticsearch bulk index format.

        { "index": { "_index": "hive-audit", "_id": "..." } }
        { "event": ... }
        """
        lines: list[str] = []
        for ev in events:
            meta = {"index": {"_index": "hive-audit", "_id": ev.get("id", "")}}
            lines.append(json.dumps(meta, ensure_ascii=False))
            lines.append(json.dumps(ev, ensure_ascii=False))

        output = "\n".join(lines)
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(output + "\n")
        return output

    def to_cloudwatch(
        self, events: list[dict[str, Any]], *, path: str | None = None
    ) -> str:
        """Export to AWS CloudWatch Logs JSON format.

        [{"timestamp": 1704067200000, "message": "..."}]
        """
        entries = [
            {
                "timestamp": int(ev.get("ts", time.time()) * 1000),
                "message": json.dumps(ev, ensure_ascii=False),
            }
            for ev in events
        ]
        output = json.dumps(entries, ensure_ascii=False, indent=2)
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(output + "\n")
        return output


__all__ = ["AuditExporter"]
