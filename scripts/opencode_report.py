#!/usr/bin/env python3
"""Inspect native OpenCode Hive observation logs (schema_version 1).

Reads JSONL event files written by the OpenCode observer plugin
(``mode: "observe"``, ``source: "opencode"``) and prints a human-readable
table grouped by project and session, or a machine-readable ``--json``
report.

This tool is strictly observational:

* It reports only what the collector recorded. It does NOT infer task
  success, cost savings, or whether Hive routing was active.
* ``cost_estimate`` values are provider-normalized estimates produced by
  OpenCode, not invoices, and carry no guarantee that the underlying
  provider usage stream was complete.
* Steps with missing usage are counted separately; unknown usage is never
  treated as free. ``full`` token/cost totals are ``null`` whenever any
  contributing value is missing or collection is incomplete; the
  ``known_subtotal`` lower bound is always reported alongside.

Fail-closed: a missing/empty data directory, or input that yields zero
valid events, exits nonzero. Malformed rows are dropped and flagged, never
silently coerced. This tool never echoes prompts, tool arguments, or error
strings.

Report limits: ``agent``/``provider_id``/``model_id`` are raw OpenCode
identifiers (not pseudonymized) and may name custom agents or providers;
``data_dir`` and collector filenames are the operator's own local paths.
Treat report output as local operational data, not a shareable artifact.

Data directory resolution order: ``--data-dir`` >
``$HIVE_OPENCODE_DATA_DIR`` > ``~/.local/share/hive/opencode``.
``HIVE_OPENCODE_DISABLED=1`` only stops collection; existing logs are
still reported, with a warning.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
SOURCE = "opencode"
MODE = "observe"

DEFAULT_DATA_DIR = Path("~/.local/share/hive/opencode")
ENV_DATA_DIR = "HIVE_OPENCODE_DATA_DIR"
ENV_DISABLED = "HIVE_OPENCODE_DISABLED"

EVENT_TYPES = frozenset({
    "session_started",
    "session_idle",
    "session_error",
    "tool_state",
    "step_usage",
    "request_observed",
    "collector_started",
    "collector_stopped",
})
SESSION_SCOPED = frozenset({
    "session_started",
    "session_idle",
    "session_error",
    "tool_state",
    "step_usage",
    "request_observed",
})
TOOL_CATEGORIES = frozenset({
    "read", "list", "grep", "bash", "edit", "write", "other",
})
TOOL_STATUSES = frozenset({"pending", "running", "completed", "error"})
TOKEN_KEYS = ("input", "output", "reasoning", "cache_read", "cache_write")
# Collector-emitted stats keys (collector_started/collector_stopped).
# Anything else is counted as unknown, never echoed.
STATS_KEYS = frozenset({"queue_max", "written", "dropped", "write_errors"})
# error_kind is a coarse classifier label from a fixed allowlist shared
# with the collector — never free text. Values outside the set coerce to
# "other" and are counted, so arbitrary error names are never echoed.
ERROR_KINDS = frozenset({
    "unknown", "other",
    "Error", "TypeError", "RangeError", "SyntaxError", "ReferenceError",
    "EvalError", "URIError", "AggregateError",
    "AbortError", "TimeoutError", "NetworkError", "ProviderAuthError",
    "RateLimitError", "QuotaExceededError", "ContextOverflowError",
    "ModelNotFoundError", "APIError",
})
# Upper bound on any echoed string field (ids, agent/provider/model).
MAX_STR_LEN = 256
# Upper bound on any accepted numeric field. Bounded inputs keep every
# aggregate finite — sums can never overflow to inf.
MAX_NUM = 1e15
# Input volume caps, enforced before/while reading. With MAX_NUM-bounded
# values, MAX_TOTAL_ROWS × TOKEN_KEYS × MAX_NUM stays far below float
# overflow, so every reported aggregate is provably finite — and memory
# stays bounded because files are stat-checked before decode.
MAX_FILES = 10_000
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_ROWS_PER_FILE = 1_000_000
MAX_TOTAL_ROWS = 5_000_000

# Fields this report understands. Anything else is counted as an unknown
# field (never echoed) so schema drift is visible without leaking content.
ENVELOPE_KEYS = frozenset({
    "schema_version", "mode", "source", "collector_version", "run_id",
    "project_id", "session_id", "event_type", "event_id", "timestamp_ms",
})
EVENT_KEYS = {
    "session_started": frozenset({"parent_session_id"}),
    "session_idle": frozenset(),
    "session_error": frozenset({"error_kind"}),
    "request_observed": frozenset({"agent", "provider_id", "model_id"}),
    "collector_started": frozenset({"stats"}),
    "collector_stopped": frozenset({"stats"}),
    "tool_state": frozenset({"tool_category", "status", "duration_ms",
                           "call_id"}),
    "step_usage": frozenset({"message_id", "part_id", "dedup_key",
                             "revision", "tokens", "cost_estimate"}),
}

EXIT_OK = 0
EXIT_NO_EVIDENCE = 2
EXIT_INTEGRITY = 3


class RowError(ValueError):
    """One JSONL row failed contract validation; it is dropped."""


def _is_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v) and len(v) <= MAX_STR_LEN


def _is_finite_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v) and abs(v) <= MAX_NUM


def _req_str(row: dict, key: str) -> str:
    v = row.get(key)
    if not _is_str(v):
        raise RowError(f"{key} missing or not a non-empty string")
    return v


def _req_num(row: dict, key: str) -> float:
    v = row.get(key)
    if not _is_finite_num(v):
        raise RowError(f"{key} missing or not a finite number")
    return v


def _opt_nonneg_num(row: dict, key: str) -> float | None:
    v = row.get(key)
    if v is None:
        return None
    if not _is_finite_num(v) or v < 0:
        raise RowError(f"{key} not a non-negative finite number")
    return v


def validate_row(row: Any) -> dict[str, Any]:
    """Validate one JSONL row against the schema_version=1 contract.

    Returns a normalized event dict. Raises RowError for anything that
    cannot be trusted — the caller drops and counts it.
    """
    if not isinstance(row, dict):
        raise RowError("row is not a JSON object")
    if row.get("schema_version") != SCHEMA_VERSION:
        raise RowError("schema_version is not 1")
    if row.get("mode") != MODE:
        raise RowError("mode is not 'observe'")
    if row.get("source") != SOURCE:
        raise RowError("source is not 'opencode'")
    event_type = row.get("event_type")
    if event_type not in EVENT_TYPES:
        raise RowError("event_type missing or unknown")

    unknown = len(set(row) - ENVELOPE_KEYS - EVENT_KEYS[event_type])

    ev: dict[str, Any] = {
        "run_id": _req_str(row, "run_id"),
        "project_id": _req_str(row, "project_id"),
        "event_type": event_type,
        "event_id": _req_str(row, "event_id"),
        "timestamp_ms": _req_num(row, "timestamp_ms"),
    }
    for opt in ("collector_version",):
        v = row.get(opt)
        if v is not None:
            if not _is_str(v):
                raise RowError(f"{opt} present but not a non-empty string")
            ev[opt] = v
    session_id = row.get("session_id")
    if event_type in SESSION_SCOPED:
        if not _is_str(session_id):
            raise RowError("session_id required for session-scoped event")
        ev["session_id"] = session_id
    elif session_id is not None:
        if not _is_str(session_id):
            raise RowError("session_id present but not a non-empty string")
        ev["session_id"] = session_id

    if event_type == "tool_state":
        cat = row.get("tool_category")
        if cat not in TOOL_CATEGORIES:
            raise RowError("tool_category missing or outside allowlist")
        status = row.get("status")
        if status not in TOOL_STATUSES:
            raise RowError("status missing or unknown")
        ev["tool_category"] = cat
        ev["status"] = status
        ev["duration_ms"] = _opt_nonneg_num(row, "duration_ms")
        call_id = row.get("call_id")
        if call_id is not None:
            if not _is_str(call_id):
                raise RowError("call_id present but not a non-empty string")
            ev["call_id"] = call_id
    elif event_type == "session_started":
        parent = row.get("parent_session_id")
        if parent is not None:
            if not _is_str(parent):
                raise RowError("parent_session_id not a non-empty string")
            ev["parent_session_id"] = parent
    elif event_type == "session_error":
        kind = row.get("error_kind")
        if kind is not None:
            # Coarse label only: fixed allowlist shared with the
            # collector; anything else coerces to "other" and is
            # counted — arbitrary error names are never echoed.
            if not _is_str(kind) or kind not in ERROR_KINDS:
                ev["error_kind"] = "other"
                ev["error_kind_coerced"] = True
            else:
                ev["error_kind"] = kind
    elif event_type == "request_observed":
        for opt in ("agent", "provider_id", "model_id"):
            v = row.get(opt)
            if v is not None:
                if not _is_str(v):
                    raise RowError(f"{opt} not a non-empty string")
                ev[opt] = v
    elif event_type in ("collector_started", "collector_stopped"):
        stats = row.get("stats")
        if stats is not None:
            if not isinstance(stats, dict):
                raise RowError("stats present but not an object")
            unknown += len(set(stats) - STATS_KEYS)
            norm_stats: dict[str, float] = {}
            for k, v in stats.items():
                if k not in STATS_KEYS:
                    continue
                if not _is_finite_num(v) or v < 0:
                    raise RowError(f"stats.{k} not a non-negative "
                                   "finite number")
                norm_stats[k] = v
            ev["stats"] = norm_stats
        else:
            ev["stats"] = {}
    elif event_type == "step_usage":
        ev["message_id"] = _req_str(row, "message_id")
        ev["part_id"] = _req_str(row, "part_id")
        dedup_key = row.get("dedup_key")
        if dedup_key is not None:
            if not _is_str(dedup_key):
                raise RowError("dedup_key not a non-empty string")
            ev["dedup_key"] = dedup_key
        revision = row.get("revision", 0)
        if not _is_finite_num(revision) or revision < 0:
            raise RowError("revision not a non-negative finite number")
        ev["revision"] = revision
        tokens = row.get("tokens")
        if tokens is not None:
            if not isinstance(tokens, dict):
                raise RowError("tokens present but not an object")
            unknown += len(set(tokens) - set(TOKEN_KEYS))
            norm: dict[str, float | None] = {}
            for k in TOKEN_KEYS:
                v = tokens.get(k)
                if v is None:
                    norm[k] = None
                elif not _is_finite_num(v) or v < 0:
                    raise RowError(f"tokens.{k} not a non-negative "
                                   "finite number")
                else:
                    norm[k] = v
            ev["tokens"] = norm
        else:
            ev["tokens"] = None
        ev["cost_estimate"] = _opt_nonneg_num(row, "cost_estimate")

    ev["unknown_fields"] = unknown
    return ev


def iter_events(data_dir: Path) -> tuple[list[dict], dict[str, Any]]:
    """Read every *.jsonl file under data_dir (sorted for determinism).

    Returns (events, integrity) where integrity counts dropped rows,
    unreadable files, and unknown fields. Never raises on bad content.
    """
    events: list[dict] = []
    integrity: dict[str, Any] = {
        "files": [],
        "files_unreadable": 0,
        "files_skipped": 0,
        "rows_total": 0,
        "rows_dropped": 0,
        "rows_over_limit": 0,
        "unknown_fields": 0,
        "error_kinds_coerced": 0,
    }
    try:
        paths = sorted(data_dir.glob("*.jsonl"))
    except OSError:
        paths = []
    for path in paths[:MAX_FILES]:
        finfo = {"file": path.name, "rows": 0, "dropped": 0}
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                integrity["files_skipped"] += 1
                integrity["files"].append(finfo)
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            integrity["files_unreadable"] += 1
            integrity["files"].append(finfo)
            continue
        for line in text.splitlines():
            if not line.strip():
                continue
            if finfo["rows"] >= MAX_ROWS_PER_FILE or \
                    integrity["rows_total"] >= MAX_TOTAL_ROWS:
                integrity["rows_over_limit"] += 1
                continue
            finfo["rows"] += 1
            integrity["rows_total"] += 1
            try:
                ev = validate_row(json.loads(line))
            except (ValueError, TypeError, OverflowError, RecursionError):
                finfo["dropped"] += 1
                integrity["rows_dropped"] += 1
                continue
            integrity["unknown_fields"] += ev.pop("unknown_fields")
            if ev.pop("error_kind_coerced", False):
                integrity["error_kinds_coerced"] += 1
            events.append(ev)
        integrity["files"].append(finfo)
    integrity["files_skipped"] += max(0, len(paths) - MAX_FILES)
    return events, integrity


def _new_session(run_id: str, project_id: str, session_id: str) -> dict:
    return {
        "run_id": run_id,
        "project_id": project_id,
        "session_id": session_id,
        "parent_session_id": None,
        "started": False,
        "idle_events": 0,
        "errors": 0,
        "error_kinds": {},
        "requests": 0,
        "requests_by_model": {},
        # Distinct tool calls keyed by call_id → latest observed state.
        # Internal only; folded into tool_calls/tool_errors/tool_open
        # below and popped before the report is emitted.
        "_tool_states": {},
        "tool_state_events": {c: 0 for c in sorted(TOOL_CATEGORIES)},
        "tool_calls": {c: 0 for c in sorted(TOOL_CATEGORIES)},
        "tool_errors": 0,
        "tool_open": 0,          # distinct calls still pending/running
        "tool_unpaired": 0,      # tool_state rows without call_id
        "steps": 0,
        "steps_with_usage": 0,
        "steps_missing_usage": 0,
        "tokens": {k: 0 for k in TOKEN_KEYS},
        "tokens_known": {k: 0 for k in TOKEN_KEYS},
        "tokens_missing": {k: 0 for k in TOKEN_KEYS},
        "cost_known": 0.0,
        "cost_known_steps": 0,
        "cost_missing": 0,
        "first_ts": None,
        "last_ts": None,
        "flags": [],
    }


def build_report(events: list[dict], integrity: dict[str, Any],
                 data_dir: Path) -> dict[str, Any]:
    """Aggregate validated events into the report document."""
    sessions: dict[tuple[str, str], dict] = {}
    runs: dict[str, dict] = {}
    steps: dict[tuple, dict] = {}
    seen_event_ids: set[tuple[str, str]] = set()
    dup_event_ids = 0
    superseded = 0

    def session_for(ev: dict) -> dict:
        key = (ev["run_id"], ev["session_id"])
        s = sessions.get(key)
        if s is None:
            s = _new_session(ev["run_id"], ev["project_id"],
                             ev["session_id"])
            sessions[key] = s
        elif s["project_id"] != ev["project_id"]:
            # Same session under two projects: keep first, flag drift.
            if "project_id_drift" not in s["flags"]:
                s["flags"].append("project_id_drift")
        ts = ev["timestamp_ms"]
        s["first_ts"] = ts if s["first_ts"] is None else min(s["first_ts"], ts)
        s["last_ts"] = ts if s["last_ts"] is None else max(s["last_ts"], ts)
        return s

    for order, ev in enumerate(events):
        run = runs.setdefault(ev["run_id"], {
            "run_id": ev["run_id"],
            "collector_started": 0,
            "collector_stopped": 0,
            "collector_stats": {},
            "flags": [],
        })
        et = ev["event_type"]
        if et == "step_usage":
            # Dedup by (run, dedup_key); dedup_key is the pseudonymous
            # session/message/part key the collector emits. Fall back to
            # the explicit triple when absent. Keep the max revision.
            key = (ev["run_id"],
                   ev.get("dedup_key") or (ev["session_id"],
                                           ev["message_id"],
                                           ev["part_id"]))
            prev = steps.get(key)
            if prev is None or (ev["revision"], ev["timestamp_ms"], order) >= \
                    (prev["revision"], prev["timestamp_ms"], prev["_order"]):
                if prev is not None:
                    superseded += 1
                ev["_order"] = order
                steps[key] = ev
            else:
                superseded += 1
            continue

        # Non-step events dedup by (run_id, event_id): the collector
        # numbers events e_1, e_2, … per run, so event_id alone collides
        # across runs and would silently drop later runs' rows.
        ekey = (ev["run_id"], ev["event_id"])
        if ekey in seen_event_ids:
            dup_event_ids += 1
            continue
        seen_event_ids.add(ekey)

        if et == "collector_started":
            run["collector_started"] += 1
            for k, v in ev["stats"].items():
                run["collector_stats"][k] = \
                    run["collector_stats"].get(k, 0) + v
        elif et == "collector_stopped":
            run["collector_stopped"] += 1
            for k, v in ev["stats"].items():
                run["collector_stats"][k] = \
                    run["collector_stats"].get(k, 0) + v
        else:
            s = session_for(ev)
            if et == "session_started":
                s["started"] = True
                if ev.get("parent_session_id"):
                    s["parent_session_id"] = ev["parent_session_id"]
            elif et == "session_idle":
                s["idle_events"] += 1
            elif et == "session_error":
                s["errors"] += 1
                kind = ev.get("error_kind") or "unknown"
                s["error_kinds"][kind] = s["error_kinds"].get(kind, 0) + 1
            elif et == "request_observed":
                s["requests"] += 1
                model = "/".join(x for x in (ev.get("provider_id"),
                                           ev.get("model_id")) if x) \
                    or "unknown"
                s["requests_by_model"][model] = \
                    s["requests_by_model"].get(model, 0) + 1
            elif et == "tool_state":
                s["tool_state_events"][ev["tool_category"]] += 1
                call_id = ev.get("call_id")
                if call_id is None:
                    # No call_id: cannot prove distinctness, so it is
                    # counted as a state event and flagged, never as a
                    # call — tool_calls stays a distinct-call count.
                    s["tool_unpaired"] += 1
                    if ev["status"] in ("pending", "running"):
                        s["tool_open"] += 1
                    elif ev["status"] == "error":
                        s["tool_errors"] += 1
                else:
                    # Latest observed state per call wins; a
                    # pending→running→completed lifecycle is one call.
                    s["_tool_states"][call_id] = {
                        "category": ev["tool_category"],
                        "status": ev["status"],
                    }

    # Fold deduplicated steps into their sessions.
    for ev in steps.values():
        s = session_for(ev)
        s["steps"] += 1
        tokens = ev["tokens"]
        has_usage = False
        if tokens is not None:
            for k in TOKEN_KEYS:
                v = tokens[k]
                if v is None:
                    s["tokens_missing"][k] += 1
                else:
                    s["tokens_known"][k] += 1
                    s["tokens"][k] += v
                    has_usage = True
        else:
            for k in TOKEN_KEYS:
                s["tokens_missing"][k] += 1
        if ev["cost_estimate"] is not None:
            s["cost_known"] += ev["cost_estimate"]
            s["cost_known_steps"] += 1
            has_usage = True
        else:
            s["cost_missing"] += 1
        if has_usage:
            s["steps_with_usage"] += 1
        else:
            s["steps_missing_usage"] += 1

    # Fold distinct tool calls (latest state per call_id) into counts.
    for s in sessions.values():
        states = s.pop("_tool_states")
        for st in states.values():
            s["tool_calls"][st["category"]] += 1
            if st["status"] == "error":
                s["tool_errors"] += 1
            elif st["status"] in ("pending", "running"):
                s["tool_open"] += 1

    # Lifecycle flags.
    for s in sessions.values():
        if not s["started"]:
            s["flags"].append("missing_session_started")
        if s["started"] and s["idle_events"] == 0:
            s["flags"].append("no_idle_observed")
        if s["tool_unpaired"] > 0:
            s["flags"].append("unpaired_tool_states")
        if s["tool_open"] > 0:
            s["flags"].append("tools_in_flight")
        s["flags"].sort()
    for run in runs.values():
        if run["collector_started"] > run["collector_stopped"]:
            run["flags"].append("collector_unclosed")
        if run["collector_stopped"] > run["collector_started"]:
            run["flags"].append("collector_stopped_without_start")
        cs = run["collector_stats"]
        if cs.get("dropped", 0) > 0 or cs.get("write_errors", 0) > 0:
            run["flags"].append("collector_reported_drops")
    projects: dict[str, dict] = {}
    for s in sessions.values():
        p = projects.setdefault(s["project_id"], {
            "project_id": s["project_id"],
            "sessions": [],
        })
        p["sessions"].append(s)
    for p in projects.values():
        p["sessions"].sort(key=lambda s: (s["first_ts"] is None,
                                          s["first_ts"] or 0,
                                          s["session_id"]))

    # Usage completeness: "full" totals are null whenever any contributing
    # value is missing or collection is incomplete — unknown is never
    # reported as zero/free. known_subtotal is always the lower bound.
    token_sub = {k: sum(s["tokens"][k] for s in sessions.values())
                 for k in TOKEN_KEYS}
    incomplete = (
        not steps or integrity["rows_dropped"] > 0
        or integrity["files_unreadable"] > 0
        or integrity["files_skipped"] > 0
        or integrity["rows_over_limit"] > 0
        or any(run["flags"] for run in runs.values())
    )
    any_token_missing = incomplete or any(
        s["tokens_missing"][k] for s in sessions.values()
        for k in TOKEN_KEYS)
    any_cost_missing = incomplete or any(
        s["cost_missing"] for s in sessions.values())

    totals = {
        "runs": len(runs),
        "projects": len(projects),
        "sessions": len(sessions),
        "steps": sum(s["steps"] for s in sessions.values()),
        "steps_with_usage": sum(s["steps_with_usage"]
                                for s in sessions.values()),
        "steps_missing_usage": sum(s["steps_missing_usage"]
                                   for s in sessions.values()),
        "tokens": {
            "known_subtotal": token_sub,
            "full": None if any_token_missing else dict(token_sub),
        },
        "tokens_known": {k: sum(s["tokens_known"][k]
                                for s in sessions.values())
                         for k in TOKEN_KEYS},
        "tokens_missing": {k: sum(s["tokens_missing"][k]
                                  for s in sessions.values())
                           for k in TOKEN_KEYS},
        "cost_estimate": {
            "known_subtotal": sum(s["cost_known"]
                                  for s in sessions.values()),
            "full": (None if any_cost_missing else
                     sum(s["cost_known"] for s in sessions.values())),
            "steps_known": sum(s["cost_known_steps"]
                               for s in sessions.values()),
            "steps_missing": sum(s["cost_missing"]
                                 for s in sessions.values()),
        },
        "tool_calls": {c: sum(s["tool_calls"][c]
                              for s in sessions.values())
                       for c in sorted(TOOL_CATEGORIES)},
        "tool_state_events": {c: sum(s["tool_state_events"][c]
                                     for s in sessions.values())
                              for c in sorted(TOOL_CATEGORIES)},
        "tool_errors": sum(s["tool_errors"] for s in sessions.values()),
        "session_errors": sum(s["errors"] for s in sessions.values()),
        "requests": sum(s["requests"] for s in sessions.values()),
    }

    integrity.update({
        "duplicate_event_ids": dup_event_ids,
        "superseded_step_revisions": superseded,
        "sessions_flagged": sum(1 for s in sessions.values() if s["flags"]),
        "runs_flagged": sum(1 for r in runs.values() if r["flags"]),
    })

    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "source": SOURCE,
        "data_dir": str(data_dir),
        "collection_disabled": os.environ.get(ENV_DISABLED) == "1",
        "totals": totals,
        "integrity": integrity,
        "runs": sorted(runs.values(), key=lambda r: r["run_id"]),
        "projects": [projects[k] for k in sorted(projects)],
        "caveats": [
            "Observed records only; no task-success or cost-savings "
            "inference.",
            "cost_estimate is provider-normalized by OpenCode, not an "
            "invoice; underlying provider usage completeness is not "
            "guaranteed.",
            "Steps with missing usage are counted separately; unknown "
            "usage is never treated as zero. Full token/cost totals are "
            "null when usage is absent, a contributing value is missing, "
            "or collection is incomplete.",
            "tool_calls counts distinct calls by latest observed state; "
            "tool_state_events counts raw lifecycle rows.",
            "agent/provider_id/model_id are raw OpenCode identifiers, "
            "not pseudonyms; data_dir and filenames are local operator "
            "paths. Treat report output as local operational data.",
            "A run flagged collector_unclosed may be missing its tail "
            "events; treat its counts as a lower bound.",
            "This report does not indicate whether Hive routing was "
            "active during collection.",
        ],
    }


def _fmt_int(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:.1f}"


def _fmt_cost(v: float | None) -> str:
    return "null" if v is None else f"${v:.4f}"


def render_text(report: dict[str, Any]) -> str:
    out: list[str] = []
    t = report["totals"]
    out.append("Hive OpenCode observation report "
               f"(schema_version={report['schema_version']}, "
               f"mode={report['mode']})")
    out.append(f"data dir: {report['data_dir']}")
    if report["collection_disabled"]:
        out.append(f"WARNING: {ENV_DISABLED}=1 — collection is disabled; "
                   "showing previously collected data only.")
    out.append("")
    out.append(f"runs {t['runs']}  projects {t['projects']}  "
               f"sessions {t['sessions']}  requests {t['requests']}  "
               f"session_errors {t['session_errors']}")
    out.append(f"steps {t['steps']}  (usage reported "
               f"{t['steps_with_usage']}, missing {t['steps_missing_usage']})")
    tok = t["tokens"]
    sub = tok["known_subtotal"]
    out.append("tokens known-subtotal  in {tin}  out {tout}  "
               "reasoning {rea}  cache_read {cr}  "
               "cache_write {cw}".format(
                   tin=_fmt_int(sub["input"]), tout=_fmt_int(sub["output"]),
                   rea=_fmt_int(sub["reasoning"]),
                   cr=_fmt_int(sub["cache_read"]),
                   cw=_fmt_int(sub["cache_write"])))
    full = tok["full"]
    out.append("tokens full  " + (
        "null (incomplete usage)" if full is None else
        "in {tin}  out {tout}  reasoning {rea}  "
        "cache_read {cr}  cache_write {cw}".format(
            tin=_fmt_int(full["input"]), tout=_fmt_int(full["output"]),
            rea=_fmt_int(full["reasoning"]),
            cr=_fmt_int(full["cache_read"]),
            cw=_fmt_int(full["cache_write"]))))
    cost = t["cost_estimate"]
    out.append(f"cost_estimate known-subtotal "
               f"{_fmt_cost(cost['known_subtotal'])}  "
               f"full {_fmt_cost(cost['full'])} "
               f"({cost['steps_known']} known, "
               f"{cost['steps_missing']} missing; estimate, not invoice)")
    tc = t["tool_calls"]
    te = t["tool_state_events"]
    out.append("tool calls  " + "  ".join(f"{c} {tc[c]}" for c in tc)
               + f"  (errors {t['tool_errors']}, "
               f"state events {sum(te.values())})")
    out.append("")

    for p in report["projects"]:
        out.append(f"project {p['project_id']}")
        out.append(f"  {'session':<24} {'steps':>5} {'usage?':>10} "
                   f"{'tokens(in/out)':>16} {'cost_est':>10} "
                   f"{'tools':>5} {'err':>4} {'idle':>4}  flags")
        for s in p["sessions"]:
            usage = f"{s['steps_with_usage']}/{s['steps']}"
            tokio = (f"{_fmt_int(s['tokens']['input'])}/"
                     f"{_fmt_int(s['tokens']['output'])}")
            flags = ",".join(s["flags"]) or "-"
            out.append(f"  {s['session_id']:<24} {s['steps']:>5} "
                       f"{usage:>10} {tokio:>16} "
                       f"{_fmt_cost(s['cost_known']):>10} "
                       f"{sum(s['tool_calls'].values()):>5} "
                       f"{s['errors'] + s['tool_errors']:>4} "
                       f"{s['idle_events']:>4}  {flags}")
        out.append("")

    ig = report["integrity"]
    out.append("integrity")
    out.append(f"  files {len(ig['files'])} "
               f"(unreadable {ig['files_unreadable']}, "
               f"skipped {ig['files_skipped']})  "
               f"rows {ig['rows_total']}  dropped {ig['rows_dropped']}  "
               f"over_limit {ig['rows_over_limit']}  "
               f"unknown_fields {ig['unknown_fields']}  "
               f"error_kinds_coerced {ig['error_kinds_coerced']}")
    out.append(f"  duplicate_event_ids {ig['duplicate_event_ids']}  "
               f"superseded_step_revisions "
               f"{ig['superseded_step_revisions']}  "
               f"flagged sessions {ig['sessions_flagged']}  "
               f"flagged runs {ig['runs_flagged']}")
    for f in ig["files"]:
        if f["dropped"] or f["rows"] == 0:
            out.append(f"  {f['file']}: {f['rows']} rows, "
                       f"{f['dropped']} dropped")
    for r in report["runs"]:
        cs = r.get("collector_stats") or {}
        if cs:
            out.append(f"  run {r['run_id']} collector stats: "
                       + "  ".join(f"{k} {_fmt_int(v)}"
                                 for k, v in sorted(cs.items())))
        if r["flags"]:
            out.append(f"  run {r['run_id']}: {','.join(r['flags'])}")
    out.append("")
    for c in report["caveats"]:
        out.append(f"! {c}")
    return "\n".join(out)


def resolve_data_dir(cli_arg: str | None) -> Path:
    if cli_arg:
        return Path(cli_arg).expanduser()
    env = os.environ.get(ENV_DATA_DIR)
    if env:
        return Path(env).expanduser()
    return DEFAULT_DATA_DIR.expanduser()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="opencode_report",
        description="Summarize Hive observation JSONL collected from "
                    "OpenCode (schema_version 1, mode=observe). "
                    "Observational only: no task-success, savings, or "
                    "routing inference.")
    ap.add_argument("--data-dir", default=None,
                    help=f"directory of collector *.jsonl files "
                         f"(default: ${ENV_DATA_DIR} or "
                         f"{DEFAULT_DATA_DIR})")
    ap.add_argument("--json", action="store_true",
                    help="emit the full report as JSON instead of the "
                         "human table")
    ap.add_argument("--strict", action="store_true",
                    help=f"exit {EXIT_INTEGRITY} when any rows were "
                         "dropped, duplicated, or superseded")
    args = ap.parse_args(argv)

    data_dir = resolve_data_dir(args.data_dir)
    if not data_dir.is_dir():
        print(f"error: no evidence — data dir {data_dir} does not exist "
              "or is not a directory", file=sys.stderr)
        return EXIT_NO_EVIDENCE

    events, integrity = iter_events(data_dir)
    if integrity["rows_total"] == 0:
        print(f"error: no evidence — no JSONL rows under {data_dir}",
              file=sys.stderr)
        return EXIT_NO_EVIDENCE
    if not events:
        print(f"error: no evidence — all {integrity['rows_total']} rows "
              f"under {data_dir} failed schema validation "
              f"({integrity['rows_dropped']} dropped)", file=sys.stderr)
        return EXIT_NO_EVIDENCE

    report = build_report(events, integrity, data_dir)
    if args.json:
        json.dump(report, sys.stdout, indent=2, sort_keys=False,
                  allow_nan=False)
        sys.stdout.write("\n")
    else:
        print(render_text(report))

    if args.strict and (integrity["rows_dropped"]
                        or integrity["duplicate_event_ids"]
                        or integrity["superseded_step_revisions"]
                        or integrity["files_unreadable"]
                        or integrity["files_skipped"]
                        or integrity["rows_over_limit"]
                        or integrity["runs_flagged"]
                        or integrity["sessions_flagged"]):
        return EXIT_INTEGRITY
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
