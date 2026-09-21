"""Offline tests for scripts/opencode_report.py — synthetic JSONL only.

The report consumes the schema_version=1 / mode="observe" /
source="opencode" contract emitted by the OpenCode collector plugin.
Every test builds its own event rows; nothing touches the real data dir,
the network, or a model.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import opencode_report as ocr  # noqa: E402

# ---------------------------------------------------------------------------
# Synthetic event helpers
# ---------------------------------------------------------------------------

_SEQ = iter(range(10_000))


def _ev(event_type: str, **kw) -> dict:
    row = {
        "schema_version": 1,
        "mode": "observe",
        "source": "opencode",
        "run_id": "run-a",
        "project_id": "proj-1",
        "event_type": event_type,
        "event_id": f"e{next(_SEQ)}",
        "timestamp_ms": 1_700_000_000_000 + next(_SEQ),
    }
    row.update(kw)
    return row


_UNSET = object()


def _step(session="sess-1", message="m1", part="p1", revision=0,
          tokens=_UNSET, cost=None, **kw):
    if tokens is _UNSET:
        tokens = {"input": 10, "output": 5, "reasoning": None,
                  "cache_read": 0, "cache_write": None}
    return _ev("step_usage", session_id=session, message_id=message,
               part_id=part, revision=revision, tokens=tokens,
               cost_estimate=cost, **kw)


def _write(data_dir: Path, rows: list, name: str = "run-a.jsonl") -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / name
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(r if isinstance(r, str) else json.dumps(r))
            fh.write("\n")
    return path


def _report(data_dir: Path) -> dict:
    events, integrity = ocr.iter_events(data_dir)
    return ocr.build_report(events, integrity, data_dir)


def _session(report: dict, session_id: str = "sess-1") -> dict:
    for p in report["projects"]:
        for s in p["sessions"]:
            if s["session_id"] == session_id:
                return s
    raise AssertionError(f"session {session_id} not in report")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_happy_path_aggregates(tmp_path):
    _write(tmp_path, [
        _ev("collector_started"),
        _ev("session_started", session_id="sess-1"),
        _ev("request_observed", session_id="sess-1"),
        _ev("tool_state", session_id="sess-1", tool_category="read",
            status="completed", duration_ms=12, call_id="c1"),
        _ev("tool_state", session_id="sess-1", tool_category="bash",
            status="error", duration_ms=40, call_id="c2"),
        _step(cost=0.001),
        _ev("session_idle", session_id="sess-1"),
        _ev("collector_stopped"),
    ])
    rep = _report(tmp_path)
    t = rep["totals"]
    assert t["sessions"] == 1 and t["steps"] == 1
    assert t["steps_with_usage"] == 1 and t["steps_missing_usage"] == 0
    assert t["tokens"]["known_subtotal"]["input"] == 10
    assert t["tokens"]["known_subtotal"]["output"] == 5
    # reasoning/cache_write are null → full total is null, not zero
    assert t["tokens"]["full"] is None
    # null token fields are missing, not zero-summed silently
    assert t["tokens_missing"]["reasoning"] == 1
    assert t["tokens_known"]["cache_read"] == 1  # explicit 0 is reported
    assert t["cost_estimate"]["known_subtotal"] == pytest.approx(0.001)
    assert t["cost_estimate"]["full"] == pytest.approx(0.001)
    assert t["tool_calls"]["read"] == 1 and t["tool_calls"]["bash"] == 1
    assert t["requests"] == 1
    s = _session(rep)
    assert s["flags"] == []
    assert rep["integrity"]["rows_dropped"] == 0

def test_dedup_key_used_for_step_dedup(tmp_path):
    _write(tmp_path, [
        _ev("session_started", session_id="sess-1"),
        _step(revision=0, dedup_key="dk-1",
              tokens={"input": 10, "output": 5, "reasoning": None,
                      "cache_read": None, "cache_write": None}),
        _step(revision=1, dedup_key="dk-1",
              tokens={"input": 99, "output": 5, "reasoning": None,
                      "cache_read": None, "cache_write": None}),
        _step(revision=0, dedup_key="dk-2"),
    ])
    rep = _report(tmp_path)
    s = _session(rep)
    assert s["steps"] == 2
    assert s["tokens"]["input"] == 99 + 10
    assert rep["integrity"]["superseded_step_revisions"] == 1


def test_confirmed_optional_fields_not_unknown(tmp_path):
    _write(tmp_path, [
        _ev("collector_started", collector_version="0.1.0",
            stats={"queue_max": 64}),
        _ev("session_started", session_id="sess-1",
            parent_session_id="sess-0"),
        _ev("request_observed", session_id="sess-1", agent="build",
            provider_id="prov", model_id="mod"),
        _ev("session_error", session_id="sess-1", error_kind="Error"),
        _ev("session_idle", session_id="sess-1"),
        _ev("collector_stopped",
            stats={"written": 5, "dropped": 0, "write_errors": 0}),
    ])
    rep = _report(tmp_path)
    assert rep["integrity"]["unknown_fields"] == 0
    s = _session(rep)
    assert s["parent_session_id"] == "sess-0"
    assert s["error_kinds"] == {"Error": 1}
    assert s["requests_by_model"] == {"prov/mod": 1}
    run = rep["runs"][0]
    assert run["collector_stats"]["written"] == 5
    assert run["flags"] == []


def test_collector_reported_drops_flagged(tmp_path):
    _write(tmp_path, [
        _ev("collector_started"),
        _ev("session_started", session_id="sess-1"),
        _ev("collector_stopped",
            stats={"written": 2, "dropped": 3, "write_errors": 1}),
    ])
    rep = _report(tmp_path)
    assert "collector_reported_drops" in rep["runs"][0]["flags"]


def test_json_output_is_parseable(tmp_path, capsys):
    _write(tmp_path, [_ev("session_started", session_id="sess-1"),
                      _step()])
    rc = ocr.main(["--data-dir", str(tmp_path), "--json"])
    assert rc == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["schema_version"] == 1
    assert doc["mode"] == "observe" and doc["source"] == "opencode"
    assert doc["totals"]["steps"] == 1
    assert any("not an invoice" in c or "not invoice" in c
               for c in doc["caveats"])


# ---------------------------------------------------------------------------
# Dedup: revised steps and duplicate event ids
# ---------------------------------------------------------------------------

def test_step_dedup_keeps_last_revision(tmp_path):
    _write(tmp_path, [
        _ev("session_started", session_id="sess-1"),
        _step(revision=0, tokens={"input": 10, "output": 5,
                                "reasoning": None, "cache_read": None,
                                "cache_write": None}, cost=0.001),
        _step(revision=2, tokens={"input": 30, "output": 9,
                                "reasoning": None, "cache_read": None,
                                "cache_write": None}, cost=0.004),
        _step(revision=1, tokens={"input": 20, "output": 7,
                                "reasoning": None, "cache_read": None,
                                "cache_write": None}, cost=0.002),
    ])
    rep = _report(tmp_path)
    s = _session(rep)
    assert s["steps"] == 1
    assert s["tokens"]["input"] == 30 and s["tokens"]["output"] == 9
    assert s["cost_known"] == pytest.approx(0.004)
    assert rep["integrity"]["superseded_step_revisions"] == 2


def test_step_dedup_scoped_by_message_and_part(tmp_path):
    _write(tmp_path, [
        _ev("session_started", session_id="sess-1"),
        _step(message="m1", part="p1"),
        _step(message="m1", part="p2"),
        _step(message="m2", part="p1"),
        _step(session="sess-2", message="m1", part="p1"),
    ])
    rep = _report(tmp_path)
    assert rep["totals"]["steps"] == 4
    assert rep["integrity"]["superseded_step_revisions"] == 0


def test_duplicate_event_ids_deduped(tmp_path):
    ev = _ev("session_idle", session_id="sess-1")
    _write(tmp_path, [
        _ev("session_started", session_id="sess-1"),
        ev, dict(ev),  # same event_id re-emitted
    ])
    rep = _report(tmp_path)
    assert _session(rep)["idle_events"] == 1
    assert rep["integrity"]["duplicate_event_ids"] == 1


# ---------------------------------------------------------------------------
# Token/cost edge values: zero, null, nonfinite, negative
# ---------------------------------------------------------------------------

def test_zero_tokens_are_reported_usage_not_missing(tmp_path):
    _write(tmp_path, [
        _ev("session_started", session_id="sess-1"),
        _step(tokens={"input": 0, "output": 0, "reasoning": 0,
                      "cache_read": 0, "cache_write": 0}, cost=0.0),
    ])
    rep = _report(tmp_path)
    s = _session(rep)
    assert s["steps_with_usage"] == 1
    assert s["steps_missing_usage"] == 0
    assert all(v == 1 for v in s["tokens_known"].values())
    # every field known → full equals the subtotal, not null
    assert rep["totals"]["tokens"]["full"]["input"] == 0
    assert rep["totals"]["cost_estimate"]["full"] == 0.0


def test_null_tokens_counted_missing_not_free(tmp_path):
    _write(tmp_path, [
        _ev("session_started", session_id="sess-1"),
        _step(tokens=None, cost=None),
    ])
    rep = _report(tmp_path)
    s = _session(rep)
    assert s["steps"] == 1
    assert s["steps_with_usage"] == 0
    assert s["steps_missing_usage"] == 1
    assert all(v == 1 for v in s["tokens_missing"].values())
    # unknown usage is never displayed as zero/free
    assert s["cost_known"] == 0.0
    assert s["cost_missing"] == 1
    assert rep["totals"]["cost_estimate"]["full"] is None
    assert rep["totals"]["tokens"]["full"] is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"),
                                 float("-inf"), -1, "12", True])
def test_invalid_token_values_drop_row(tmp_path, bad):
    _write(tmp_path, [
        _ev("session_started", session_id="sess-1"),
        _step(tokens={"input": bad, "output": 5, "reasoning": None,
                      "cache_read": None, "cache_write": None}),
    ])
    rep = _report(tmp_path)
    assert rep["totals"]["steps"] == 0
    assert rep["integrity"]["rows_dropped"] == 1


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.5, "x"])
def test_invalid_cost_drops_row(tmp_path, bad):
    _write(tmp_path, [
        _ev("session_started", session_id="sess-1"),
        _step(cost=bad),
    ])
    rep = _report(tmp_path)
    assert rep["totals"]["steps"] == 0
    assert rep["integrity"]["rows_dropped"] == 1


@pytest.mark.parametrize("bad", [float("nan"), -3, "fast"])
def test_invalid_duration_drops_row(tmp_path, bad):
    _write(tmp_path, [
        _ev("session_started", session_id="sess-1"),
        _ev("tool_state", session_id="sess-1", tool_category="read",
            status="completed", duration_ms=bad),
    ])
    rep = _report(tmp_path)
    assert rep["integrity"]["rows_dropped"] == 1
    assert rep["totals"]["tool_calls"]["read"] == 0


# ---------------------------------------------------------------------------
# Errors vs success; lifecycle flags on partial logs
# ---------------------------------------------------------------------------

def test_errors_counted_separately_from_success(tmp_path):
    _write(tmp_path, [
        _ev("session_started", session_id="sess-1"),
        _ev("session_error", session_id="sess-1"),
        _ev("session_error", session_id="sess-1"),
        _ev("tool_state", session_id="sess-1", tool_category="edit",
            status="error", call_id="c1"),
        _ev("tool_state", session_id="sess-1", tool_category="edit",
            status="completed", call_id="c2"),
        _ev("session_idle", session_id="sess-1"),
    ])
    s = _session(_report(tmp_path))
    assert s["errors"] == 2
    assert s["tool_errors"] == 1
    assert s["tool_calls"]["edit"] == 2


def test_missing_session_started_flagged(tmp_path):
    _write(tmp_path, [_step(), _ev("session_idle", session_id="sess-1")])
    s = _session(_report(tmp_path))
    assert "missing_session_started" in s["flags"]


def test_no_idle_flagged(tmp_path):
    _write(tmp_path, [_ev("session_started", session_id="sess-1"),
                      _step()])
    s = _session(_report(tmp_path))
    assert "no_idle_observed" in s["flags"]


def test_tools_in_flight_flagged(tmp_path):
    _write(tmp_path, [
        _ev("session_started", session_id="sess-1"),
        _ev("tool_state", session_id="sess-1", tool_category="bash",
            status="running", call_id="c1"),
        _ev("session_idle", session_id="sess-1"),
    ])
    s = _session(_report(tmp_path))
    assert "tools_in_flight" in s["flags"]


def test_collector_unclosed_flagged(tmp_path):
    _write(tmp_path, [_ev("collector_started"),
                      _ev("session_started", session_id="sess-1")])
    rep = _report(tmp_path)
    run = rep["runs"][0]
    assert "collector_unclosed" in run["flags"]


def test_tool_category_allowlist_enforced(tmp_path):
    _write(tmp_path, [
        _ev("session_started", session_id="sess-1"),
        _ev("tool_state", session_id="sess-1", tool_category="rm_rf",
            status="completed"),
    ])
    rep = _report(tmp_path)
    assert rep["integrity"]["rows_dropped"] == 1
    assert rep["totals"]["tool_calls"]["other"] == 0


# ---------------------------------------------------------------------------
# Malformed rows and fail-closed behavior
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("row", [
    "not json {",
    json.dumps([1, 2, 3]),
    json.dumps({"schema_version": 2, "mode": "observe",
                "source": "opencode", "run_id": "r", "project_id": "p",
                "event_type": "session_idle", "event_id": "e",
                "timestamp_ms": 1, "session_id": "s"}),
    json.dumps({"schema_version": 1, "mode": "train",
                "source": "opencode", "run_id": "r", "project_id": "p",
                "event_type": "session_idle", "event_id": "e",
                "timestamp_ms": 1, "session_id": "s"}),
    json.dumps({"schema_version": 1, "mode": "observe",
                "source": "omp", "run_id": "r", "project_id": "p",
                "event_type": "session_idle", "event_id": "e",
                "timestamp_ms": 1, "session_id": "s"}),
    json.dumps({"schema_version": 1, "mode": "observe",
                "source": "opencode", "run_id": "r", "project_id": "p",
                "event_type": "explode", "event_id": "e",
                "timestamp_ms": 1}),
    json.dumps({"schema_version": 1, "mode": "observe",
                "source": "opencode", "run_id": "r", "project_id": "p",
                "event_type": "session_idle", "timestamp_ms": 1,
                "session_id": "s"}),  # no event_id
    json.dumps({"schema_version": 1, "mode": "observe",
                "source": "opencode", "run_id": "r", "project_id": "p",
                "event_type": "session_idle", "event_id": "e",
                "timestamp_ms": 1}),  # no session_id
])
def test_malformed_rows_dropped_and_flagged(tmp_path, row):
    _write(tmp_path, [row, _ev("session_started", session_id="sess-1")])
    rep = _report(tmp_path)
    assert rep["integrity"]["rows_dropped"] == 1
    assert rep["totals"]["sessions"] == 1


def test_missing_dir_fails_closed(tmp_path):
    rc = ocr.main(["--data-dir", str(tmp_path / "nope")])
    assert rc == ocr.EXIT_NO_EVIDENCE


def test_empty_dir_fails_closed(tmp_path, capsys):
    rc = ocr.main(["--data-dir", str(tmp_path)])
    assert rc == ocr.EXIT_NO_EVIDENCE
    assert "no evidence" in capsys.readouterr().err


def test_all_malformed_fails_closed(tmp_path, capsys):
    _write(tmp_path, ["garbage", "{"])
    rc = ocr.main(["--data-dir", str(tmp_path)])
    assert rc == ocr.EXIT_NO_EVIDENCE
    assert "no evidence" in capsys.readouterr().err


def test_strict_exit_on_dropped_rows(tmp_path):
    _write(tmp_path, ["bad row",
                      json.dumps(_ev("session_started",
                                     session_id="sess-1"))])
    assert ocr.main(["--data-dir", str(tmp_path)]) == ocr.EXIT_OK
    assert ocr.main(["--data-dir", str(tmp_path), "--strict"]) == \
        ocr.EXIT_INTEGRITY


# ---------------------------------------------------------------------------
# Privacy: unknown fields counted, never echoed
# ---------------------------------------------------------------------------

def test_unknown_fields_counted_not_echoed(tmp_path, capsys):
    # Neutral canary: must still be absent from the output, but is not
    # secret-shaped so credential scanners do not flag the test fixture.
    secret = "CANARY-not-a-credential-00000"
    row = _ev("session_started", session_id="sess-1")
    row["prompt_text"] = secret
    row["nested"] = {"path": "/home/tester/secret"}
    _write(tmp_path, [row, _step()])
    rc = ocr.main(["--data-dir", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert secret not in out
    assert "prompt_text" not in out
    assert "/home/tester" not in out
    rep = _report(tmp_path)
    assert rep["integrity"]["unknown_fields"] == 2

def test_error_strings_never_echoed(tmp_path, capsys):
    row = _ev("session_error", session_id="sess-1")
    row["error"] = "boom at /home/tester/private/file.py"
    _write(tmp_path, [_ev("session_started", session_id="sess-1"), row])
    ocr.main(["--data-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert "boom" not in out and "/home/tester" not in out


# ---------------------------------------------------------------------------
# Env-var resolution
# ---------------------------------------------------------------------------

def test_env_data_dir_used_when_no_flag(tmp_path, monkeypatch, capsys):
    _write(tmp_path, [_ev("session_started", session_id="sess-1")])
    monkeypatch.setenv(ocr.ENV_DATA_DIR, str(tmp_path))
    rc = ocr.main([])
    assert rc == 0
    assert str(tmp_path) in capsys.readouterr().out


def test_flag_overrides_env(tmp_path, monkeypatch, capsys):
    env_dir = tmp_path / "env"
    flag_dir = tmp_path / "flag"
    _write(env_dir, [_ev("session_started", session_id="sess-env")])
    _write(flag_dir, [_ev("session_started", session_id="sess-flag")])
    monkeypatch.setenv(ocr.ENV_DATA_DIR, str(env_dir))
    ocr.main(["--data-dir", str(flag_dir)])
    out = capsys.readouterr().out
    assert "sess-flag" in out and "sess-env" not in out


def test_disabled_env_warns_but_still_reports(tmp_path, monkeypatch,
                                             capsys):
    _write(tmp_path, [_ev("session_started", session_id="sess-1")])
    monkeypatch.setenv(ocr.ENV_DISABLED, "1")
    rc = ocr.main(["--data-dir", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "disabled" in out.lower()
    assert "sess-1" in out


# ---------------------------------------------------------------------------
# Multi-file / multi-project grouping
# ---------------------------------------------------------------------------

def test_grouping_by_project_and_run(tmp_path):
    _write(tmp_path, [
        _ev("session_started", session_id="s1"),
        _step(session="s1"),
    ], name="run-a.jsonl")
    _write(tmp_path, [
        _ev("session_started", session_id="s2", run_id="run-b",
            project_id="proj-2"),
        _step(session="s2", run_id="run-b", project_id="proj-2",
              tokens={"input": 7, "output": 3, "reasoning": None,
                      "cache_read": None, "cache_write": None}),
    ], name="run-b.jsonl")
    rep = _report(tmp_path)
    assert rep["totals"]["runs"] == 2
    assert rep["totals"]["projects"] == 2
    assert rep["totals"]["sessions"] == 2
    assert rep["totals"]["tokens"]["known_subtotal"]["input"] == 17
    assert [p["project_id"] for p in rep["projects"]] == \
        ["proj-1", "proj-2"]


# ---------------------------------------------------------------------------
# Cross-run event-id collisions (real collector numbering is per-run)
# ---------------------------------------------------------------------------

def test_cross_run_event_ids_do_not_collide(tmp_path):
    # The collector emits e_1, e_2, … per plugin instance. Two run files
    # with identical event ids must both be counted — no silent run loss.
    _write(tmp_path, [
        _ev("collector_started", event_id="e_1"),
        _ev("session_started", session_id="s1", event_id="e_2"),
        _ev("session_idle", session_id="s1", event_id="e_3"),
        _ev("collector_stopped", event_id="e_4"),
    ], name="run-a.jsonl")
    _write(tmp_path, [
        _ev("collector_started", run_id="run-b", project_id="proj-2",
            event_id="e_1"),
        _ev("session_started", run_id="run-b", project_id="proj-2",
            session_id="s2", event_id="e_2"),
        _ev("session_idle", run_id="run-b", project_id="proj-2",
            session_id="s2", event_id="e_3"),
        _ev("collector_stopped", run_id="run-b", project_id="proj-2",
            event_id="e_4"),
    ], name="run-b.jsonl")
    rep = _report(tmp_path)
    assert rep["totals"]["runs"] == 2
    assert rep["totals"]["sessions"] == 2
    assert rep["integrity"]["duplicate_event_ids"] == 0
    assert rep["runs"][0]["collector_started"] == 1
    assert rep["runs"][1]["collector_started"] == 1


def test_same_run_duplicate_event_ids_still_deduped(tmp_path):
    ev = _ev("session_idle", session_id="sess-1", event_id="e_9")
    _write(tmp_path, [
        _ev("session_started", session_id="sess-1"),
        ev, dict(ev),
    ])
    rep = _report(tmp_path)
    assert _session(rep)["idle_events"] == 1
    assert rep["integrity"]["duplicate_event_ids"] == 1


# ---------------------------------------------------------------------------
# Tool-call lifecycle: distinct calls by latest state, not state rows
# ---------------------------------------------------------------------------

def test_tool_lifecycle_counts_one_call(tmp_path):
    _write(tmp_path, [
        _ev("session_started", session_id="sess-1"),
        _ev("tool_state", session_id="sess-1", tool_category="bash",
            status="pending", call_id="c1"),
        _ev("tool_state", session_id="sess-1", tool_category="bash",
            status="running", call_id="c1"),
        _ev("tool_state", session_id="sess-1", tool_category="bash",
            status="completed", duration_ms=42, call_id="c1"),
        _ev("session_idle", session_id="sess-1"),
    ])
    rep = _report(tmp_path)
    s = _session(rep)
    assert s["tool_calls"]["bash"] == 1
    assert s["tool_state_events"]["bash"] == 3
    assert s["tool_open"] == 0
    assert "tools_in_flight" not in s["flags"]
    assert rep["totals"]["tool_calls"]["bash"] == 1


def test_tool_latest_state_wins(tmp_path):
    _write(tmp_path, [
        _ev("session_started", session_id="sess-1"),
        _ev("tool_state", session_id="sess-1", tool_category="edit",
            status="running", call_id="c1"),
        _ev("tool_state", session_id="sess-1", tool_category="edit",
            status="error", call_id="c1"),
        _ev("session_idle", session_id="sess-1"),
    ])
    s = _session(_report(tmp_path))
    assert s["tool_calls"]["edit"] == 1
    assert s["tool_errors"] == 1
    assert s["tool_open"] == 0


def test_unpaired_tool_states_flagged_not_counted(tmp_path):
    _write(tmp_path, [
        _ev("session_started", session_id="sess-1"),
        _ev("tool_state", session_id="sess-1", tool_category="read",
            status="completed", duration_ms=5),
        _ev("session_idle", session_id="sess-1"),
    ])
    s = _session(_report(tmp_path))
    assert s["tool_unpaired"] == 1
    assert s["tool_state_events"]["read"] == 1
    assert s["tool_calls"]["read"] == 0
    assert "unpaired_tool_states" in s["flags"]


# ---------------------------------------------------------------------------
# Usage completeness contract: full=null when incomplete
# ---------------------------------------------------------------------------

def test_full_totals_null_when_collection_incomplete(tmp_path):
    _write(tmp_path, [
        _ev("collector_started"),
        _ev("session_started", session_id="sess-1"),
        _step(cost=0.01),
        _ev("session_idle", session_id="sess-1"),
        # no collector_stopped → collector_unclosed → incomplete
    ])
    rep = _report(tmp_path)
    assert "collector_unclosed" in rep["runs"][0]["flags"]
    assert rep["totals"]["cost_estimate"]["full"] is None
    assert rep["totals"]["cost_estimate"]["known_subtotal"] == \
        pytest.approx(0.01)


def test_full_totals_null_when_rows_dropped(tmp_path):
    _write(tmp_path, [
        "garbage row",
        json.dumps(_ev("session_started", session_id="sess-1")),
        json.dumps(_step(cost=0.01)),
    ])
    rep = _report(tmp_path)
    assert rep["integrity"]["rows_dropped"] == 1
    assert rep["totals"]["cost_estimate"]["full"] is None


# ---------------------------------------------------------------------------
# Strict mode covers incompleteness, not just row drops
# ---------------------------------------------------------------------------

def test_strict_exit_on_unclosed_collector(tmp_path):
    _write(tmp_path, [
        _ev("collector_started"),
        _ev("session_started", session_id="sess-1"),
        _ev("session_idle", session_id="sess-1"),
    ])
    assert ocr.main(["--data-dir", str(tmp_path)]) == ocr.EXIT_OK
    assert ocr.main(["--data-dir", str(tmp_path), "--strict"]) == \
        ocr.EXIT_INTEGRITY


def test_strict_exit_on_collector_reported_drops(tmp_path):
    _write(tmp_path, [
        _ev("collector_started"),
        _ev("session_started", session_id="sess-1"),
        _ev("session_idle", session_id="sess-1"),
        _ev("collector_stopped",
            stats={"written": 4, "dropped": 2, "write_errors": 0}),
    ])
    assert ocr.main(["--data-dir", str(tmp_path)]) == ocr.EXIT_OK
    assert ocr.main(["--data-dir", str(tmp_path), "--strict"]) == \
        ocr.EXIT_INTEGRITY


# ---------------------------------------------------------------------------
# Malformed-input containment: never raises on bad content
# ---------------------------------------------------------------------------

def test_invalid_utf8_file_counted_unreadable(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "bad.jsonl").write_bytes(b"\xff\xfe not utf8 \x00")
    _write(tmp_path, [_ev("session_started", session_id="sess-1")],
           name="good.jsonl")
    rep = _report(tmp_path)
    assert rep["integrity"]["files_unreadable"] == 1
    assert rep["totals"]["sessions"] == 1


@pytest.mark.parametrize("field,value", [
    ("event_type", ["session_idle"]),
    ("event_type", {"x": 1}),
    ("tool_category", ["read"]),
    ("status", {"s": 1}),
    ("timestamp_ms", 10**400),          # int too huge for float
    ("timestamp_ms", 1e300),            # beyond MAX_NUM
])
def test_malformed_types_dropped_not_raised(tmp_path, field, value):
    row = _ev("session_idle", session_id="sess-1")
    if field in ("tool_category", "status"):
        row = _ev("tool_state", session_id="sess-1",
                  tool_category="read", status="completed")
    row[field] = value
    _write(tmp_path, [row, _ev("session_started", session_id="sess-1")])
    rep = _report(tmp_path)
    assert rep["integrity"]["rows_dropped"] == 1
    assert rep["totals"]["sessions"] == 1


def test_recursion_error_during_parse_is_dropped_not_raised(tmp_path,
                                                            monkeypatch):
    # Deterministic containment check: a RecursionError from parsing must be
    # counted and dropped, never escape iter_events. (A fixed nesting depth is
    # not a reliable trigger — CPython's C scanner accepts ~2k levels and the
    # limit is interpreter/platform dependent — so force the exception.)
    def boom(_text):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(ocr.json, "loads", boom)
    _write(tmp_path, [_ev("session_idle", session_id="sess-1"),
                      _ev("session_started", session_id="sess-1")])
    rep = _report(tmp_path)
    assert rep["integrity"]["rows_dropped"] == 2
    assert rep["integrity"]["rows_total"] == 2
    assert rep["totals"]["sessions"] == 0


def test_deeply_nested_json_contained(tmp_path):
    # Far beyond any plausible parser nesting budget: either the parser
    # rejects it (dropped and counted) or it parses. Either way it must not
    # raise and must not corrupt the following row.
    depth = 200_000
    nested = '{"x":' * depth + "null" + "}" * depth
    row = '{"schema_version":1,"mode":"observe","source":"opencode",' \
          '"run_id":"r","project_id":"p","event_type":"session_idle",' \
          f'"event_id":"e1","timestamp_ms":1,"session_id":"s",' \
          f'"payload":{nested}}}'
    _write(tmp_path, [row,
                      json.dumps(_ev("session_started",
                                     session_id="sess-1"))])
    events, integrity = ocr.iter_events(tmp_path)  # must not raise
    assert integrity["rows_total"] == 2
    assert integrity["rows_dropped"] in (0, 1)
    assert len(events) == 2 - integrity["rows_dropped"]
    rep = ocr.build_report(events, integrity, tmp_path)
    assert rep["totals"]["sessions"] == 1


# ---------------------------------------------------------------------------
# Stats/error-kind allowlists: arbitrary keys never echoed
# ---------------------------------------------------------------------------

def test_unknown_stats_keys_counted_not_echoed(tmp_path, capsys):
    _write(tmp_path, [
        _ev("collector_started",
            stats={"queue_max": 64, "secret_key": 99}),
        _ev("session_started", session_id="sess-1"),
        _ev("collector_stopped",
            stats={"written": 3, "dropped": 0, "write_errors": 0}),
    ])
    rep = _report(tmp_path)
    run = rep["runs"][0]
    assert run["collector_stats"].get("queue_max") == 64
    assert "secret_key" not in run["collector_stats"]
    assert rep["integrity"]["unknown_fields"] == 1
    ocr.main(["--data-dir", str(tmp_path)])
    assert "secret_key" not in capsys.readouterr().out


def test_arbitrary_error_kind_coerced_to_other(tmp_path):
    _write(tmp_path, [
        _ev("session_started", session_id="sess-1"),
        _ev("session_error", session_id="sess-1",
            error_kind="CustomVendorSecretError9f4b2c"),
        _ev("session_error", session_id="sess-1",
            error_kind="ProviderAuthError"),
    ])
    rep = _report(tmp_path)
    s = _session(rep)
    assert s["error_kinds"] == {"other": 1, "ProviderAuthError": 1}
    assert rep["integrity"]["error_kinds_coerced"] == 1
    ocr.main(["--data-dir", str(tmp_path)])
