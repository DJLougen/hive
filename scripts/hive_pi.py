#!/usr/bin/env python3
"""Hive bridge for the Pi coding agent (schema_version 1).

Two subcommands:

``compress``
    Reads one JSON object from stdin::

        {"schema_version": 1, "items": [{"id": "...", "text": "..."}]}

    and writes one JSON object to stdout::

        {"schema_version": 1,
         "items": [{"id": "...", "text": "...", "label": "..."}]}

    Only successful test-run output is eligible for compression: the text
    must end in a pytest-like success summary (``N passed``) and contain
    no failure counts, tracebacks, patches, or code dumps. Eligible text
    is compressed with the in-repo ``hive.rule_fast.RuleFastHoneyComb``
    (no ML model, no network, no HiveStack optional backends) using
    ``Message(role="tool", content_type=ContentType.TOOL_RESULT_TEST)``;
    the original text is kept whenever the compressed form is not
    strictly shorter. ``label`` is the classifier label (``"distill"``)
    when the text changed and ``"unchanged"`` otherwise.

    Fail-closed: input over 1 MiB, more than 128 items, non-JSON, wrong
    schema, missing/duplicate ids, or non-string text exits nonzero with
    a constant error line on stderr and nothing on stdout. The caller
    must then keep the original messages.

``report [--data-dir DIR] [--json] [--strict]``
    Summarizes Pi collector JSONL (``source: "pi"``, ``mode`` in
    ``observe``/``compress``). Strictly observational: it reports only
    what the collector recorded — no task-success inference, no token or
    monetary savings claims. Context byte counters are per-event payload
    observations, not unique bytes saved. Missing usage is reported
    separately and never treated as zero; full totals are ``null`` when
    any contributing value is missing, with known subtotals reported
    alongside. Malformed rows are dropped and counted, never coerced.
    Pseudonymous ids only — this tool never echoes prompts, paths, tool
    arguments, or error strings.

Data directory resolution order for ``report``: ``--data-dir`` >
``$HIVE_PI_DATA_DIR`` > ``~/.local/share/hive/pi/events``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from hive.rule_fast import ContentType, Message, RuleFastHoneyComb

    _IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - import guard
    ContentType = Message = RuleFastHoneyComb = None  # type: ignore[assignment]
    _IMPORT_ERROR = exc

SCHEMA_VERSION = 1
SOURCE = "pi"
MODES = frozenset({"observe", "compress"})

MAX_INPUT_BYTES = 1024 * 1024
MAX_ITEMS = 128

DEFAULT_DATA_DIR = Path("~/.local/share/hive/pi/events")
ENV_DATA_DIR = "HIVE_PI_DATA_DIR"

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_INVALID = 2
EXIT_NO_EVIDENCE = 2
EXIT_INTEGRITY = 3

ERR_INVALID = "error: invalid compress request"
ERR_UNAVAILABLE = "error: compress unavailable"

# ---------------------------------------------------------------------------
# compress: strict input validation
# ---------------------------------------------------------------------------

_ITEM_KEYS = frozenset({"id", "text"})
_REQUEST_KEYS = frozenset({"schema_version", "items"})


class _Invalid(ValueError):
    """Compress request failed contract validation."""


def _is_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v)


def _is_finite_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v)


def validate_request(payload: Any) -> list[dict[str, str]]:
    """Validate the compress request; return the item list.

    Raises _Invalid for anything outside the contract — the caller exits
    nonzero with a constant error and the JS side keeps original text.
    """
    if not isinstance(payload, dict):
        raise _Invalid("request is not a JSON object")
    if set(payload) - _REQUEST_KEYS:
        raise _Invalid("request has unknown fields")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise _Invalid("schema_version is not 1")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise _Invalid("items missing or not a non-empty array")
    if len(items) > MAX_ITEMS:
        raise _Invalid("too many items")
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for it in items:
        if not isinstance(it, dict) or set(it) != _ITEM_KEYS:
            raise _Invalid("item must be an object with exactly id+text")
        item_id, text = it["id"], it["text"]
        if not _is_str(item_id):
            raise _Invalid("item id missing or not a non-empty string")
        if item_id in seen:
            raise _Invalid("duplicate item id")
        if not isinstance(text, str):
            raise _Invalid("item text is not a string")
        seen.add(item_id)
        out.append({"id": item_id, "text": text})
    return out


# ---------------------------------------------------------------------------
# compress: conservative eligibility — successful test summaries only
# ---------------------------------------------------------------------------

# A genuine pytest success summary line, e.g.
# "===== 67 passed, 2 warnings in 0.95s =====". "0 passed" is not a
# success signal; failure counts in the same line are still caught by
# _RE_FAILCOUNT below.
_RE_PYTEST_SUMMARY = re.compile(
    r"^=+\s*[^=\n]*\b[1-9]\d*\s+passed\b[^=\n]*\s*=+\s*$",
    re.IGNORECASE | re.MULTILINE)
# unittest footer: "Ran 5 tests in 0.01s" plus a final "OK"/"OK (…)".
_RE_UNITTEST_RAN = re.compile(r"^Ran [1-9]\d* tests?\b", re.MULTILINE)
_RE_UNITTEST_OK = re.compile(r"^OK(?:\s*\([^)\n]*\))?\s*$",
                             re.MULTILINE)
# Any nonzero failure/error count anywhere disqualifies the text.
_RE_FAILCOUNT = re.compile(r"\b[1-9]\d*\s+(?:failed|errors?)\b",
                           re.IGNORECASE)
# Test-runner framing: pytest border/summary lines, session headers,
# unittest/go-test style result lines.
_RE_RUNNER = re.compile(
    r"=+\s*[^=\n]*\bpassed\b[^=\n]*\s*=+"
    r"|test session starts"
    r"|collected \d+ items?"
    r"|^Ran \d+ tests?\b"
    r"|running \d+ tests?"
    r"|^test result:",
    re.IGNORECASE | re.MULTILINE,
)
# A structurally complete single-line pytest progress record:
# "<nodeid> <STATUS> [ xx%]". Only non-failure statuses are recognized
# — a line ending FAILED/ERROR is never treated as progress. The
# nodeid must contain "::"; the parametrized-id portion is matched
# loosely because ids may contain spaces and literal "\n" text, but the
# line must still END with the status token (plus optional percentage).
_RE_PYTEST_PROGRESS = re.compile(
    r"^\s*\S+::.*\s+(?:PASSED|SKIPPED|XFAIL|XPASS)"
    r"\s*(?:\[\s*\d{1,3}%\])?\s*$",
    re.MULTILINE)
# unittest verbose progress: "test_x (pkg.Mod) ... ok". Only
# non-failure outcomes; "... FAIL"/"... ERROR" lines are never
# recognized.
_RE_UNITTEST_PROGRESS = re.compile(
    r"^\s*\S.*\s\.\.\.\s(?:ok|skipped(?:\s+.*)?|expected failure|"
    r"unexpected success)\s*$",
    re.IGNORECASE | re.MULTILINE)
_RE_TRACEBACK = re.compile(
    r"Traceback \(most recent call last\)|^\w+(?:Error|Exception): ",
    re.MULTILINE,
)
_RE_PATCH = re.compile(r"^[-+]{3} |^diff --git|^@@ ", re.MULTILINE)
_RE_FAIL_LINE = re.compile(
    r"^\s*(FAILED|ERROR|FAIL:|FAIL\b|not ok\b|✗|✘)", re.MULTILINE)
_RE_ASSERT = re.compile(
    r"AssertionError|AssertionFailedError|assertion failed|panic:",
    re.IGNORECASE)
_RE_CODE_LINE = re.compile(
    r"^\s*(class |def |import |from |export |function |pub |fn |struct |"
    r"enum |impl |#include|package |var |const |let )",
    re.MULTILINE)

_TAIL_LINES = 15
_MAX_CODE_LINES = 0


def _sanitize_successful_test_log(text: str) -> str | None:
    """Return `text` minus recognized runner progress lines, or None.

    None means the text is not a clearly successful test-run log and
    must be kept verbatim. The returned text drops only positively
    identified pytest/unittest progress records — headers, warnings,
    unknown diagnostics, and the final summary are preserved exactly.
    """
    if not text:
        return None
    # Strip only structurally complete single-line progress records.
    # Parametrized ids may embed literal "\n" text containing words like
    # "1 failed" or "Traceback" — those live inside the id on ONE line
    # and are removed with it. A line ending FAILED/ERROR never matches
    # the progress grammar, so real failure rows survive untouched.
    cleaned = "\n".join(
        ln for ln in text.splitlines()
        if not (_RE_PYTEST_PROGRESS.match(ln)
                or _RE_UNITTEST_PROGRESS.match(ln)))
    # Independent genuine success signal on the remainder: a real
    # pytest summary border line or a unittest Ran/OK footer near the
    # end of the log.
    tail = [ln for ln in cleaned.splitlines() if ln.strip()][
        -_TAIL_LINES:]
    pytest_ok = any(_RE_PYTEST_SUMMARY.match(ln) for ln in tail)
    unittest_ok = (_RE_UNITTEST_RAN.search(cleaned) is not None
                   and any(_RE_UNITTEST_OK.match(ln) for ln in tail))
    if not (pytest_ok or unittest_ok):
        return None
    if not _RE_RUNNER.search(cleaned):
        return None
    if _RE_FAILCOUNT.search(cleaned):
        return None
    if _RE_TRACEBACK.search(cleaned):
        return None
    if _RE_PATCH.search(cleaned):
        return None
    if _RE_FAIL_LINE.search(cleaned):
        return None
    if _RE_ASSERT.search(cleaned):
        return None
    if "```" in cleaned or "~~~" in cleaned:
        return None
    code_lines = sum(1 for ln in cleaned.splitlines()
                     if _RE_CODE_LINE.match(ln))
    if code_lines > _MAX_CODE_LINES:
        return None
    return cleaned


def compressible_text(text: str) -> bool:
    """True only for text that is clearly a successful test-run log.

    Conservative on purpose: anything that looks like a failure trace, a
    patch, a code dump, or generic prose stays verbatim even if it
    happens to contain the word "passed".
    """
    return _sanitize_successful_test_log(text) is not None

def compress_items(items: list[dict[str, str]],
                   comb: Any | None = None) -> list[dict[str, str]]:
    """Compress eligible item texts; everything else passes through."""
    if comb is None:
        comb = RuleFastHoneyComb()
    out: list[dict[str, str]] = []
    for it in items:
        text = it["text"]
        label = "unchanged"
        sanitized = _sanitize_successful_test_log(text)
        if sanitized is not None:
            res = comb.process(Message(
                role="tool", content=sanitized,
                content_type=ContentType.TOOL_RESULT_TEST))
            # The shared compressor can drop warnings and arbitrary stdout.
            # Only accept its projection if every retained line survives;
            # otherwise remove progress records alone, preserving diagnostics.
            projected = res.content
            if not all(line in projected for line in sanitized.splitlines()
                       if line.strip()):
                projected = "[test] " + sanitized
            if len(projected.encode("utf-8")) < len(text.encode("utf-8")):
                text = projected
                label = "distill"
        out.append({"id": it["id"], "text": text, "label": label})
    return out


def _compress_main() -> int:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        print(ERR_INVALID, file=sys.stderr)
        return EXIT_INVALID
    try:
        payload = json.loads(raw.decode("utf-8"))
        items = validate_request(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, _Invalid):

        print(ERR_INVALID, file=sys.stderr)
        return EXIT_INVALID
    if _IMPORT_ERROR is not None:
        print(ERR_UNAVAILABLE, file=sys.stderr)
        return EXIT_INTERNAL
    try:
        out = compress_items(items)
    except Exception:
        print(ERR_UNAVAILABLE, file=sys.stderr)
        return EXIT_INTERNAL
    json.dump({"schema_version": SCHEMA_VERSION, "items": out},
              sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    return EXIT_OK


# ---------------------------------------------------------------------------
# report: JSONL validation
# ---------------------------------------------------------------------------

EVENT_TYPES = frozenset({
    "collector_started",
    "session_started",
    "collector_stopped",
    "model_usage",
    "tool_result",
    "context",
})
SESSION_SCOPED = frozenset({
    "session_started", "model_usage", "tool_result", "context",
})
TOOL_CATEGORIES = frozenset({
    "read", "list", "grep", "bash", "edit", "write", "other",
})
BRIDGE_STATUSES = frozenset({
    "inactive", "skipped", "ok", "error", "timeout",
})
TOKEN_KEYS = ("input", "output", "cache_read", "cache_write", "total")
# Shared with integrations/pi/hive.ts STOP_REASONS: the collector passes
# the host's stopReason through verbatim when it is in this enum and
# emits null otherwise. "other" is the reporter's bucket for null and
# for unrecognized non-null strings; only the latter count as coerced.
STOP_REASONS = frozenset({
    "pending", "stop", "length", "toolUse", "error", "aborted",
    "deferred", "other",
})
# Stats keys are event-specific: collector_started emits queue_max,
# collector_stopped emits written/dropped. A key valid on one event is
# still unknown on the other — counted, never echoed.
STATS_KEYS = {
    "collector_started": frozenset({"queue_max"}),
    "collector_stopped": frozenset({"written", "dropped"}),
}

# Optional per-context-event counters emitted by the JS bridge cache.
# Absent on old rows; when present each must be a non-negative int.
CONTEXT_COUNTER_KEYS = ("bridge_calls", "cache_hits", "cache_misses")

ENVELOPE_KEYS = frozenset({
    "schema_version", "source", "mode", "event_type", "event_id",
    "run_id", "project_id", "session_id", "timestamp_ms",
})
EVENT_KEYS = {
    "collector_started": frozenset({"stats"}),
    "collector_stopped": frozenset({"stats"}),
    "session_started": frozenset(),
    "model_usage": frozenset({"message_id", "tokens", "cost_estimate",
                              "stop_reason"}),
    "tool_result": frozenset({"call_id", "tool_category", "is_error"}),
    "context": frozenset({"input_bytes", "output_bytes", "changed_items",
                          "bridge_status"} | set(CONTEXT_COUNTER_KEYS)),
}


class RowError(ValueError):
    """One JSONL row failed contract validation; it is dropped."""


def _req_str(row: dict, key: str) -> str:
    v = row.get(key)
    if not _is_str(v):
        raise RowError(f"{key} missing or not a non-empty string")
    return v


def _req_nonneg_num(row: dict, key: str) -> float:
    v = row.get(key)
    if not _is_finite_num(v) or v < 0:
        raise RowError(f"{key} missing or not a non-negative finite number")
    return v


def _opt_nonneg_num(row: dict, key: str) -> float | None:
    v = row.get(key)
    if v is None:
        return None
    if not _is_finite_num(v) or v < 0:
        raise RowError(f"{key} present but not a non-negative finite number")
    return v


def _opt_nonneg_int(row: dict, key: str) -> int | None:
    v = row.get(key)
    if v is None:
        return None
    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
        raise RowError(f"{key} present but not a non-negative integer")
    return v


def _sanitize_stop_reason(v: Any) -> tuple[str, bool]:
    """Map stop_reason onto the known vocabulary.

    Returns (reason, coerced). The collector emits the host stopReason
    verbatim when it is in the shared enum and null otherwise; null is a
    valid contract value and reports as "other" without coercion.
    Unrecognized non-null values — including provider-specific strings —
    collapse to "other" and count as coerced so raw provider text is
    never echoed.
    """
    if v is None:
        return "other", False
    if isinstance(v, str) and v in STOP_REASONS:
        return v, False
    return "other", True


def validate_row(row: Any) -> dict[str, Any]:
    """Validate one JSONL row against the schema_version=1 Pi contract.

    Returns a normalized event dict. Raises RowError for anything that
    cannot be trusted — the caller drops and counts it.
    """
    if not isinstance(row, dict):
        raise RowError("row is not a JSON object")
    if row.get("schema_version") != SCHEMA_VERSION:
        raise RowError("schema_version is not 1")
    if row.get("source") != SOURCE:
        raise RowError("source is not 'pi'")
    if row.get("mode") not in MODES:
        raise RowError("mode is not 'observe' or 'compress'")
    event_type = row.get("event_type")
    if event_type not in EVENT_TYPES:
        raise RowError("event_type missing or unknown")

    ev: dict[str, Any] = {
        "run_id": _req_str(row, "run_id"),
        "project_id": _req_str(row, "project_id"),
        "event_type": event_type,
        "event_id": _req_str(row, "event_id"),
        "timestamp_ms": _req_nonneg_num(row, "timestamp_ms"),
        "mode": row["mode"],
    }
    session_id = row.get("session_id")
    if session_id is not None:
        if not _is_str(session_id):
            raise RowError("session_id present but not a non-empty string")
        ev["session_id"] = session_id
    else:
        # Contract: session_id is nullable when the collector cannot
        # resolve one. Session-scoped rows group under None.
        ev["session_id"] = None

    unknown = len(set(row) - ENVELOPE_KEYS - EVENT_KEYS[event_type])

    if event_type in ("collector_started", "collector_stopped"):
        stats = row.get("stats")
        if stats is not None:
            if not isinstance(stats, dict):
                raise RowError("stats present but not an object")
            allowed = STATS_KEYS[event_type]
            unknown += len(set(stats) - allowed)
            norm_stats: dict[str, float] = {}
            for k, v in stats.items():
                if k not in allowed:
                    continue
                if not isinstance(k, str):
                    raise RowError("stats key is not a string")
                if not _is_finite_num(v) or v < 0:
                    raise RowError("stats value not a non-negative "
                                   "finite number")
                norm_stats[k] = v
            ev["stats"] = norm_stats
        else:
            ev["stats"] = {}
    elif event_type == "model_usage":
        ev["message_id"] = _req_str(row, "message_id")
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
                    raise RowError("tokens value not a non-negative "
                                   "finite number or null")
                else:
                    norm[k] = v
            ev["tokens"] = norm
        else:
            ev["tokens"] = None
        ev["cost_estimate"] = _opt_nonneg_num(row, "cost_estimate")
        reason, coerced = _sanitize_stop_reason(row.get("stop_reason"))
        ev["stop_reason"] = reason
        ev["stop_reason_coerced"] = coerced
    elif event_type == "tool_result":
        cat = row.get("tool_category")
        if cat not in TOOL_CATEGORIES:
            raise RowError("tool_category missing or outside allowlist")
        is_error = row.get("is_error")
        if not isinstance(is_error, bool):
            raise RowError("is_error missing or not a boolean")
        ev["tool_category"] = cat
        ev["is_error"] = is_error
        call_id = row.get("call_id")
        if call_id is not None:
            if not _is_str(call_id):
                raise RowError("call_id present but not a non-empty string")
            ev["call_id"] = call_id
    elif event_type == "context":
        for k in ("input_bytes", "output_bytes", "changed_items"):
            ev[k] = _req_nonneg_num(row, k)
        status = row.get("bridge_status")
        if status not in BRIDGE_STATUSES:
            raise RowError("bridge_status missing or unknown")
        ev["bridge_status"] = status
        for k in CONTEXT_COUNTER_KEYS:
            ev[k] = _opt_nonneg_int(row, k)

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
        "rows_total": 0,
        "rows_dropped": 0,
        "unknown_fields": 0,
        "stop_reasons_coerced": 0,
    }
    for path in sorted(data_dir.glob("*.jsonl")):
        finfo = {"file": path.name, "rows": 0, "dropped": 0}
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            integrity["files_unreadable"] += 1
            integrity["files"].append(finfo)
            continue
        for line in text.splitlines():
            if not line.strip():
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
            if ev.pop("stop_reason_coerced", False):
                integrity["stop_reasons_coerced"] += 1
            events.append(ev)
        integrity["files"].append(finfo)
    return events, integrity


# ---------------------------------------------------------------------------
# report: aggregation
# ---------------------------------------------------------------------------

def _new_session(run_id: str, project_id: str,
                 session_id: str | None) -> dict:
    return {
        "run_id": run_id,
        "project_id": project_id,
        "session_id": session_id,
        "started": False,
        "tool_calls": {c: 0 for c in sorted(TOOL_CATEGORIES)},
        "tool_errors": 0,
        "tool_unpaired": 0,      # tool_result rows without call_id
        "usage_messages": 0,
        "usage_missing": 0,      # messages with no usage data at all
        "tokens": {k: 0 for k in TOKEN_KEYS},
        "tokens_known": {k: 0 for k in TOKEN_KEYS},
        "tokens_missing": {k: 0 for k in TOKEN_KEYS},
        "cost_known": 0.0,
        "cost_known_messages": 0,
        "cost_missing": 0,
        "stop_reasons": {},
        "context_events": 0,
        "context_input_bytes": 0,
        "context_output_bytes": 0,
        "context_changed_items": 0,
        "bridge_status": {s: 0 for s in sorted(BRIDGE_STATUSES)},
        "context_bridge": {
            k: {"known": 0, "missing": 0} for k in CONTEXT_COUNTER_KEYS
        },
        "first_ts": None,
        "last_ts": None,
        "flags": [],
    }


def build_report(events: list[dict], integrity: dict[str, Any],
                 data_dir: Path) -> dict[str, Any]:
    """Aggregate validated events into the report document."""
    sessions: dict[tuple[str, str | None], dict] = {}
    runs: dict[str, dict] = {}
    usage: dict[tuple, dict] = {}
    seen_event_ids: set[str] = set()
    dup_event_ids = 0
    dup_usage = 0

    def session_for(ev: dict) -> dict:
        key = (ev["project_id"], ev["session_id"])
        s = sessions.get(key)
        if s is None:
            s = _new_session(ev["run_id"], ev["project_id"],
                             ev["session_id"])
            sessions[key] = s
        ts = ev["timestamp_ms"]
        s["first_ts"] = ts if s["first_ts"] is None else min(s["first_ts"], ts)
        s["last_ts"] = ts if s["last_ts"] is None else max(s["last_ts"], ts)
        return s

    for ev in events:
        run = runs.setdefault(ev["run_id"], {
            "run_id": ev["run_id"],
            "collector_started": 0,
            "collector_stopped": 0,
            "collector_stats": {},
            "flags": [],
        })
        et = ev["event_type"]
        if et == "model_usage":
            # Dedup by (project, session, message): collector restarts can
            # re-emit the same assistant message usage. Keep the first.
            key = (ev["project_id"], ev["session_id"], ev["message_id"])
            if key in usage:
                dup_usage += 1
                continue
            usage[key] = ev
            continue

        if ev["event_id"] in seen_event_ids:
            dup_event_ids += 1
            continue
        seen_event_ids.add(ev["event_id"])

        if et == "collector_started":
            run["collector_started"] += 1
            for k, v in ev["stats"].items():
                # queue_max is a config constant, not a counter.
                if k == "queue_max":
                    run["collector_stats"][k] = max(
                        run["collector_stats"].get(k, 0), v)
                else:
                    run["collector_stats"][k] = \
                        run["collector_stats"].get(k, 0) + v
        elif et == "collector_stopped":
            run["collector_stopped"] += 1
            for k, v in ev["stats"].items():
                run["collector_stats"][k] = \
                    run["collector_stats"].get(k, 0) + v
        elif et == "session_started":
            session_for(ev)["started"] = True
        elif et == "tool_result":
            s = session_for(ev)
            s["tool_calls"][ev["tool_category"]] += 1
            if ev["is_error"]:
                s["tool_errors"] += 1
            if "call_id" not in ev:
                s["tool_unpaired"] += 1
        elif et == "context":
            s = session_for(ev)
            s["context_events"] += 1
            s["context_input_bytes"] += ev["input_bytes"]
            s["context_output_bytes"] += ev["output_bytes"]
            s["context_changed_items"] += ev["changed_items"]
            s["bridge_status"][ev["bridge_status"]] += 1
            for k in CONTEXT_COUNTER_KEYS:
                v = ev[k]
                if v is None:
                    s["context_bridge"][k]["missing"] += 1
                else:
                    s["context_bridge"][k]["known"] += v

    # Fold deduplicated usage rows into their sessions.
    for ev in usage.values():
        s = session_for(ev)
        s["usage_messages"] += 1
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
            s["cost_known_messages"] += 1
            has_usage = True
        else:
            s["cost_missing"] += 1
        if not has_usage:
            s["usage_missing"] += 1
        reason = ev["stop_reason"]
        s["stop_reasons"][reason] = s["stop_reasons"].get(reason, 0) + 1

    # Lifecycle flags.
    for s in sessions.values():
        if not s["started"]:
            s["flags"].append("missing_session_started")
        if s["session_id"] is None:
            s["flags"].append("session_id_unavailable")
        s["flags"].sort()
    for run in runs.values():
        if run["collector_started"] > run["collector_stopped"]:
            run["flags"].append("collector_unclosed")
        if run["collector_stopped"] > run["collector_started"]:
            run["flags"].append("collector_stopped_without_start")
        if run["collector_stats"].get("dropped", 0) > 0:
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
                                          s["session_id"] or ""))


    token_sub = {k: sum(s["tokens"][k] for s in sessions.values())
                 for k in TOKEN_KEYS}
    any_token_missing = any(
        s["tokens_missing"][k] for s in sessions.values()
        for k in TOKEN_KEYS)
    any_cost_missing = any(s["cost_missing"] for s in sessions.values())
    incomplete = (
        not usage or integrity["rows_dropped"] > 0
        or integrity["files_unreadable"] > 0
        or any(run["flags"] for run in runs.values())
    )
    any_token_missing = any_token_missing or incomplete
    any_cost_missing = any_cost_missing or incomplete

    totals = {
        "runs": len(runs),
        "projects": len(projects),
        "sessions": len(sessions),
        "usage_messages": sum(s["usage_messages"]
                              for s in sessions.values()),
        "usage_missing": sum(s["usage_missing"]
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
            "messages_known": sum(s["cost_known_messages"]
                                  for s in sessions.values()),
            "messages_missing": sum(s["cost_missing"]
                                    for s in sessions.values()),
        },
        "tool_calls": {c: sum(s["tool_calls"][c]
                              for s in sessions.values())
                       for c in sorted(TOOL_CATEGORIES)},
        "tool_errors": sum(s["tool_errors"] for s in sessions.values()),
        "context": {
            "events": sum(s["context_events"] for s in sessions.values()),
            "input_bytes": sum(s["context_input_bytes"]
                               for s in sessions.values()),
            "output_bytes": sum(s["context_output_bytes"]
                                for s in sessions.values()),
            "changed_items": sum(s["context_changed_items"]
                                 for s in sessions.values()),
            "bridge_status": {b: sum(s["bridge_status"][b]
                                     for s in sessions.values())
                              for b in sorted(BRIDGE_STATUSES)},
            # Optional JS bridge counters. "full" is null whenever any
            # context event omitted the field or collection is
            # incomplete — unknown is never reported as zero.
            "bridge": {
                k: {
                    "known_subtotal": sum(
                        s["context_bridge"][k]["known"]
                        for s in sessions.values()),
                    "full": (None if incomplete or any(
                        s["context_bridge"][k]["missing"]
                        for s in sessions.values()) else sum(
                        s["context_bridge"][k]["known"]
                        for s in sessions.values())),
                    "events_missing": sum(
                        s["context_bridge"][k]["missing"]
                        for s in sessions.values()),
                } for k in CONTEXT_COUNTER_KEYS
            },
        },
        "events_by_mode": {m: sum(1 for e in events if e["mode"] == m)
                           for m in sorted(MODES)},
    }

    integrity.update({
        "duplicate_event_ids": dup_event_ids,
        "duplicate_usage_messages": dup_usage,
        "sessions_flagged": sum(1 for s in sessions.values() if s["flags"]),
        "runs_flagged": sum(1 for r in runs.values() if r["flags"]),
    })

    return {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE,
        "data_dir": str(data_dir),
        "totals": totals,
        "integrity": integrity,
        "runs": sorted(runs.values(), key=lambda r: r["run_id"]),
        "projects": [projects[k] for k in sorted(projects)],
        "caveats": [
            "Observed records only; no task-success inference.",
            "cost_estimate is a provider-normalized estimate, not an "
            "invoice; underlying provider usage completeness is not "
            "guaranteed.",
            "Messages with missing usage are counted separately; unknown "
            "usage is never treated as zero. Full token/cost totals are "
            "null when usage is absent, a contributing value is missing, "
            "or collection is incomplete.",
            "Context input_bytes/output_bytes are per-event payload "
            "observations covering every candidate block's "
            "original/result bytes, including blocks passed through "
            "unchanged — not unique bytes saved; repeated contexts are "
            "counted each time they are observed.",
            "Context bridge counters (bridge_calls/cache_hits/"
            "cache_misses) are optional per-event fields; a 'full' "
            "total is null when any contributing event omits it or "
            "collection is incomplete.",
            "A run flagged collector_unclosed may be missing its tail "
            "events; treat its counts as a lower bound.",
        ],
    }


# ---------------------------------------------------------------------------
# report: text rendering
# ---------------------------------------------------------------------------

def _fmt_int(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:.1f}"


def _fmt_cost(v: float | None) -> str:
    return "null" if v is None else f"${v:.4f}"


def render_text(report: dict[str, Any]) -> str:
    out: list[str] = []
    t = report["totals"]
    out.append("Hive Pi observation report "
               f"(schema_version={report['schema_version']})")
    out.append(f"data dir: {report['data_dir']}")
    out.append("")
    out.append(f"runs {t['runs']}  projects {t['projects']}  "
               f"sessions {t['sessions']}  "
               f"usage_messages {t['usage_messages']} "
               f"(missing usage {t['usage_missing']})")
    tok = t["tokens"]
    sub = tok["known_subtotal"]
    out.append("tokens known-subtotal  in {tin}  out {tout}  "
               "cache_read {cr}  cache_write {cw}  total {tt}".format(
                   tin=_fmt_int(sub["input"]), tout=_fmt_int(sub["output"]),
                   cr=_fmt_int(sub["cache_read"]),
                   cw=_fmt_int(sub["cache_write"]),
                   tt=_fmt_int(sub["total"])))
    full = tok["full"]
    out.append("tokens full  " + (
        "null (incomplete usage)" if full is None else
        "in {tin}  out {tout}  total {tt}".format(
            tin=_fmt_int(full["input"]), tout=_fmt_int(full["output"]),
            tt=_fmt_int(full["total"]))))
    cost = t["cost_estimate"]
    out.append(f"cost_estimate known-subtotal "
               f"{_fmt_cost(cost['known_subtotal'])}  "
               f"full {_fmt_cost(cost['full'])} "
               f"({cost['messages_known']} known, "
               f"{cost['messages_missing']} missing; estimate, "
               "not invoice)")
    tc = t["tool_calls"]
    out.append("tool calls  " + "  ".join(f"{c} {tc[c]}" for c in tc)
               + f"  (errors {t['tool_errors']})")
    ctx = t["context"]
    out.append(f"context events {ctx['events']}  "
               f"input_bytes {_fmt_int(ctx['input_bytes'])}  "
               f"output_bytes {_fmt_int(ctx['output_bytes'])}  "
               f"changed_items {_fmt_int(ctx['changed_items'])} "
               "(observations, not savings)")
    out.append("bridge_status  " + "  ".join(
        f"{b} {ctx['bridge_status'][b]}" for b in ctx["bridge_status"]))
    br = ctx.get("bridge") or {}
    if br:
        def _fmt_opt(v: float | None) -> str:
            return "null" if v is None else _fmt_int(v)
        out.append("bridge counters  " + "  ".join(
            f"{k} {_fmt_int(br[k]['known_subtotal'])} known, "
            f"full {_fmt_opt(br[k]['full'])} "
            f"({br[k]['events_missing']} events missing)"
            for k in br))
    out.append("")

    for p in report["projects"]:
        out.append(f"project {p['project_id']}")
        out.append(f"  {'session':<24} {'usage':>5} {'miss':>5} "
                   f"{'tokens(in/out)':>16} {'cost_est':>10} "
                   f"{'tools':>5} {'err':>4} {'ctx':>4}  flags")
        for s in p["sessions"]:
            sid = s["session_id"] or "(none)"
            tokio = (f"{_fmt_int(s['tokens']['input'])}/"
                     f"{_fmt_int(s['tokens']['output'])}")
            flags = ",".join(s["flags"]) or "-"
            out.append(f"  {sid:<24} {s['usage_messages']:>5} "
                       f"{s['usage_missing']:>5} {tokio:>16} "
                       f"{_fmt_cost(s['cost_known']):>10} "
                       f"{sum(s['tool_calls'].values()):>5} "
                       f"{s['tool_errors']:>4} "
                       f"{s['context_events']:>4}  {flags}")
        out.append("")

    ig = report["integrity"]
    out.append("integrity")
    out.append(f"  files {len(ig['files'])} "
               f"(unreadable {ig['files_unreadable']})  "
               f"rows {ig['rows_total']}  dropped {ig['rows_dropped']}  "
               f"unknown_fields {ig['unknown_fields']}  "
               f"stop_reasons_coerced {ig['stop_reasons_coerced']}")
    out.append(f"  duplicate_event_ids {ig['duplicate_event_ids']}  "
               f"duplicate_usage_messages "
               f"{ig['duplicate_usage_messages']}  "
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


def _report_main(args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args.data_dir)
    if not data_dir.is_dir():
        print("error: no evidence — data dir does not exist or is not "
              "a directory", file=sys.stderr)
        return EXIT_NO_EVIDENCE

    events, integrity = iter_events(data_dir)
    if integrity["rows_total"] == 0:
        print("error: no evidence — no JSONL rows in data dir",
              file=sys.stderr)
        return EXIT_NO_EVIDENCE
    if not events:
        print(f"error: no evidence — all {integrity['rows_total']} rows "
              "failed schema validation", file=sys.stderr)
        return EXIT_NO_EVIDENCE

    report = build_report(events, integrity, data_dir)
    if args.json:
        json.dump(report, sys.stdout, indent=2, sort_keys=False)
        sys.stdout.write("\n")
    else:
        print(render_text(report))

    if args.strict and (integrity["rows_dropped"]
                        or integrity["duplicate_event_ids"]
                        or integrity["duplicate_usage_messages"]
                        or integrity["files_unreadable"]
                        or integrity["runs_flagged"]):
        return EXIT_INTEGRITY
    return EXIT_OK


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="hive_pi",
        description="Hive bridge for Pi: 'compress' filters stdin tool "
                    "output through RuleFastHoneyComb (successful test "
                    "summaries only); 'report' summarizes Pi collector "
                    "JSONL. Observational only: no task-success or "
                    "savings inference.")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "compress",
        help="read a compress request JSON from stdin, write the "
             "response JSON to stdout; exits nonzero with a constant "
             "error on any malformed input (caller keeps originals)")

    rp = sub.add_parser(
        "report",
        help="summarize Pi collector *.jsonl files (schema_version 1, "
             "source 'pi')")
    rp.add_argument("--data-dir", default=None,
                    help=f"directory of collector *.jsonl files "
                         f"(default: ${ENV_DATA_DIR} or "
                         f"{DEFAULT_DATA_DIR})")
    rp.add_argument("--json", action="store_true",
                    help="emit the full report as JSON instead of the "
                         "human table")
    rp.add_argument("--strict", action="store_true",
                    help=f"exit {EXIT_INTEGRITY} when any rows were "
                         "dropped or duplicated")
    args = ap.parse_args(argv)

    if args.command == "compress":
        return _compress_main()
    return _report_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
