"""Comparison-record sinks.

Every semantic decision can be emitted as one JSONL record. Those records are
the raw material for three things the integration plan calls for:

* a **Jev benchmark corpus** (what Jev decided, at what confidence, at what cost),
* a **d-Jeff training/eval corpus** (the same states, with the teacher
  distribution and — where known — the action the agent actually took),
* a **paired Jev-vs-d-Jeff comparison** (same state, same schema, two backends).

Two rules the plan is explicit about, and which this module enforces:

1. **Jev is a teacher, not truth.** Records carry ``target_provenance`` so a
   downstream trainer cannot mistake a teacher distribution for a gold label.
2. **Never split one episode across train/test.** Records carry ``group_id``
   (episode id) so splits can be made group-wise.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger("hive.semantic")


class JsonlRecordSink:
    """Append comparison records as JSON lines.

    Writes are best-effort: a failure here must never break routing, so errors
    are logged and swallowed (the caller already treats the sink as telemetry).
    """

    def __init__(self, path: str | Path, *, fsync: bool = False) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fsync = fsync
        self.count = 0

    def __call__(self, record: dict[str, Any]) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
                if self.fsync:
                    fh.flush()
            self.count += 1
        except OSError as exc:  # pragma: no cover - disk issues
            _log.warning("semantic record sink failed to write: %s", exc)


def comparison_record(
    *,
    state: dict[str, Any],
    questions: dict[str, Any],
    prediction: dict[str, Any] | None,
    backend: str | None,
    model_revision: str | None,
    selected_tool: str | None,
    accepted: bool,
    actual_action: str | None = None,
    outcome: str | None = None,
    latency_ms: float | None = None,
    schema_version: str = "hive-routing-v1",
    group_id: str | None = None,
) -> dict[str, Any]:
    """Build one portable comparison record.

    ``prediction`` holds the full distribution — soft targets are only useful if
    the whole distribution is kept, not just the argmax.
    """
    return {
        "schema_version": schema_version,
        "group_id": group_id,
        "state": state,
        "questions": questions,
        "backend": backend,
        "model_revision": model_revision,
        "prediction": prediction,
        "selected_tool": selected_tool,
        "accepted_by_hive": accepted,
        "actual_action": actual_action,
        "outcome": outcome,
        "latency_ms": latency_ms,
        # Provenance of each supervision signal, so a trainer cannot confuse a
        # teacher distribution with a ground-truth label.
        "target_provenance": {
            "soft_distribution": f"{backend}_teacher" if backend else None,
            "actual_action": "agent_execution" if actual_action else None,
        },
    }


__all__ = ["JsonlRecordSink", "comparison_record"]
