"""Offline tests for scripts/hive_pi.py — synthetic payloads/JSONL only.

The compress tests exercise the real CLI via subprocess (stdin/stdout
contract, exit codes, constant error text). The report tests feed
synthetic JSONL through iter_events/build_report/main, mirroring
tests/test_opencode_report.py conventions.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import hive_pi as hp  # noqa: E402

SCRIPT = ROOT / "scripts" / "hive_pi.py"

# ---------------------------------------------------------------------------
# compress helpers
# ---------------------------------------------------------------------------


def _pytest_log(passes: int = 12, noise: int = 400) -> str:
    """A realistic successful pytest run: header + progress noise + summary."""
    lines = [
        "============================= test session starts "
        "=============================",
        "platform darwin -- Python 3.12.4, pytest-8.3.2, pluggy-1.5.0",
        "rootdir: /work/proj",
        f"collected {passes} items",
        "",
    ]
    for i in range(passes):
        lines.append(f"tests/test_mod.py::test_case_{i:03d} PASSED")
    lines.extend("." for _ in range(noise))
    lines.append(f"============================= {passes} passed in 0.42s "
                 "=============================")
    return "\n".join(lines)


def _run_compress(payload: bytes | str) -> subprocess.CompletedProcess:
    data = payload if isinstance(payload, bytes) else payload.encode()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "compress"],
        input=data, capture_output=True, timeout=30, env=env)


def _compress_ok(items: list[dict]) -> dict:
    proc = _run_compress(json.dumps(
        {"schema_version": 1, "items": items}))
    assert proc.returncode == 0, proc.stderr.decode()
    assert proc.stderr == b""
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# compress: real compression and pass-through
# ---------------------------------------------------------------------------


def test_compress_real_test_output(tmp_path):
    log = _pytest_log()
    out = _compress_ok([{"id": "a", "text": log}])
    assert out["schema_version"] == 1
    item = out["items"][0]
    assert item["id"] == "a"
    assert item["label"] == "distill"
    assert len(item["text"]) < len(log)
    assert "12 passed" in item["text"]


def test_compress_mixed_items_preserve_order_and_ids():
    log = _pytest_log()
    prose = "Please refactor the parser to handle empty input. " * 60
    out = _compress_ok([
        {"id": "x1", "text": log},
        {"id": "x2", "text": prose},
        {"id": "x3", "text": log},
    ])
    assert [i["id"] for i in out["items"]] == ["x1", "x2", "x3"]
    assert out["items"][0]["label"] == "distill"
    assert out["items"][1]["label"] == "unchanged"
    assert out["items"][1]["text"] == prose
    assert out["items"][2]["label"] == "distill"


@pytest.mark.parametrize("text", [
    # Code dump that merely mentions tests passing.
    "def test_ok():\n    assert True\n\nclass Foo:\n    pass\n\n"
    "import os\nfrom sys import path\n# 3 passed\n" * 40,
    # Failure trace containing a passed count.
    "============================= test session starts "
    "=============================\n"
    + "tests/test_a.py::test_x PASSED\n" * 30
    + "Traceback (most recent call last):\n  File \"x.py\", line 1\n"
      "ValueError: boom\n===== 1 failed, 3 passed in 0.1s =====\n",
    # Prompt-like prose containing "passed".
    ("The user asked whether the bill passed committee and if the "
     "tests passed; please summarize. " * 80),
    # Patch/diff content.
    ("--- a/f.py\n+++ b/f.py\n@@ -1,2 +1,2 @@\n-x\n+y\n" * 60)
    + "\n===== 9 passed in 0.2s =====\n",
    # Generic stdout that happens to contain "1 passed" mid-log.
    ("deploy step 1 passed\ndeploy step 2 running\n" * 120),
])
def test_compress_keeps_ineligible_text_verbatim(text):
    out = _compress_ok([{"id": "k", "text": text}])
    item = out["items"][0]
    assert item["label"] == "unchanged"
    assert item["text"] == text


def test_compress_short_summary_not_shorter_kept():
    text = "===== 1 passed in 0.01s ====="
    out = _compress_ok([{"id": "s", "text": text}])
    assert out["items"][0]["label"] == "unchanged"
    assert out["items"][0]["text"] == text


def _pytest_progress(nodeid: str, status: str = "PASSED",
                     pct: int = 50) -> str:
    return f"{nodeid} {status:<26} [{pct:3d}%]"


def test_compress_param_id_with_failure_snippets():
    """Parametrized ids may embed literal \\n-escaped text containing
    '1 failed', 'Traceback', or patch markers — the whole progress line
    is recognized and stripped, so those words must not disqualify the
    log nor be inlined into the summary."""
    lines = [
        "============================= test session starts "
        "=============================",
        "platform darwin -- Python 3.12.4, pytest-8.3.2",
        "collected 3 items",
        "",
        _pytest_progress("tests/test_a.py::test_plain"),
        _pytest_progress(
            "tests/test_a.py::test_x[1 failed\\nTraceback (most recent "
            "call last)\\nValueError: boom]"),
        _pytest_progress(
            "tests/test_a.py::test_y[--- a/f.py\\n+++ b/f.py\\n@@ -1 +1 @@]"),
        "============================== 3 passed in 0.1s "
        "==============================",
    ]
    log = "\n".join(lines)
    out = _compress_ok([{"id": "p", "text": log}])
    item = out["items"][0]
    assert item["label"] == "distill"
    assert "3 passed" in item["text"]
    # The giant parametrized ids must not survive into the summary.
    assert "Traceback" not in item["text"]
    assert "1 failed" not in item["text"]
    assert "test_plain" not in item["text"]


def test_compress_real_failures_adjacent_stay_verbatim():
    """A log with a genuine FAILED row + traceback + mixed summary is
    not a successful log even though most progress lines are PASSED."""
    log = (
        "============================= test session starts "
        "=============================\n"
        + _pytest_progress("tests/test_a.py::test_ok") + "\n"
        + _pytest_progress("tests/test_a.py::test_bad", "FAILED", 60)
        + "\n"
        "=================================== FAILURES "
        "===================================\n"
        "_____________________________ test_bad "
        "_____________________________\n"
        "Traceback (most recent call last):\n"
        "  File \"tests/test_a.py\", line 3, in test_bad\n"
        "    assert False\n"
        "AssertionError\n"
        "=========================== short test summary info "
        "===========================\n"
        "FAILED tests/test_a.py::test_bad - assert False\n"
        "====================== 1 failed, 1 passed in 0.1s "
        "======================\n")
    out = _compress_ok([{"id": "f", "text": log}])
    item = out["items"][0]
    assert item["label"] == "unchanged"
    assert item["text"] == log


def test_compress_failed_progress_line_without_traceback_verbatim():
    """A FAILED progress row is never stripped; the mixed summary
    disqualifies the log on its own."""
    log = (
        "============================= test session starts "
        "=============================\n"
        + _pytest_progress("tests/test_a.py::test_ok") + "\n"
        + _pytest_progress("tests/test_a.py::test_bad", "FAILED", 60)
        + "\n"
        "====================== 1 failed, 1 passed in 0.1s "
        "======================\n")
    out = _compress_ok([{"id": "f", "text": log}])
    assert out["items"][0]["text"] == log


def test_compress_malformed_progress_line_preserved():
    """A line that merely resembles a progress record (trailing junk
    after the status) is not stripped; it survives in the compressed
    output as an unknown diagnostic."""
    weird = "tests/test_a.py::test_x PASSED [ 50%] trailing garbage"
    log = (
        "============================= test session starts "
        "=============================\n"
        + _pytest_progress("tests/test_a.py::test_ok") + "\n"
        + weird + "\n"
        "============================== 2 passed in 0.1s "
        "==============================\n")
    out = _compress_ok([{"id": "m", "text": log}])
    item = out["items"][0]
    assert item["label"] == "distill"
    assert weird in item["text"]


def test_compress_source_patch_mixed_verbatim():
    """Patch/source content inside an otherwise successful log keeps
    the whole input unchanged."""
    log = (
        "============================= test session starts "
        "=============================\n"
        + _pytest_progress("tests/test_a.py::test_ok") + "\n"
        "--- a/f.py\n+++ b/f.py\n@@ -1,2 +1,2 @@\n-x\n+y\n"
        "============================== 1 passed in 0.1s "
        "==============================\n")
    out = _compress_ok([{"id": "s", "text": log}])
    assert out["items"][0]["text"] == log


def test_compress_unittest_ok_log():
    """A genuine unittest success footer (Ran N tests + OK) is
    compressible; '... ok' progress lines are stripped."""
    log = (
        "test_a (tests.test_mod.TestA) ... ok\n"
        "test_b (tests.test_mod.TestA) ... ok\n"
        "test_c (tests.test_mod.TestA) ... skipped 'not today'\n"
        "test_d (tests.test_mod.TestA) ... expected failure\n"
        "\nRan 4 tests in 0.01s\n\nOK (skipped=1, expected failures=1)\n")
    out = _compress_ok([{"id": "u", "text": log}])
    item = out["items"][0]
    assert item["label"] == "distill"
    assert "OK" in item["text"]
    assert "test_a" not in item["text"]


def test_compress_unittest_failed_log_verbatim():
    """unittest failure output (FAIL rows, traceback, FAILED footer)
    stays verbatim."""
    log = (
        "test_a (tests.test_mod.TestA) ... FAIL\n"
        "\n"
        "======================================================================\n"
        "FAIL: test_a (tests.test_mod.TestA)\n"
        "----------------------------------------------------------------------\n"
        "Traceback (most recent call last):\n"
        "  File \"tests/test_mod.py\", line 4, in test_a\n"
        "    self.assertTrue(False)\n"
        "AssertionError: False is not true\n"
        "\n"
        "----------------------------------------------------------------------\n"
        "Ran 1 test in 0.01s\n\nFAILED (failures=1)\n")
    out = _compress_ok([{"id": "u", "text": log}])
    item = out["items"][0]
    assert item["label"] == "unchanged"
    assert item["text"] == log


def test_compress_unicode_and_summary_preserved():
    """Unicode in nodeids/ids is fine; the final summary line is always
    retained verbatim in the compressed output."""
    log = (
        "============================= test session starts "
        "=============================\n"
        + _pytest_progress("tests/test_a.py::test_x[✓-héllo-世界]")
        + "\n"
        + _pytest_progress("tests/test_a.py::test_y[✗-nö]") + "\n"
        "============================== 2 passed in 0.1s "
        "==============================\n")
    out = _compress_ok([{"id": "u", "text": log}])
    item = out["items"][0]
    assert item["label"] == "distill"
    assert "2 passed in 0.1s" in item["text"]
    assert "✓-héllo" not in item["text"]


# ---------------------------------------------------------------------------
# compress: malformed / oversize payloads fail closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload", [
    b"",
    b"not json {",
    b"[1,2,3]",
    json.dumps({"schema_version": 2, "items": [{"id": "a", "text": "t"}]}),
    json.dumps({"schema_version": 1, "items": []}),
    json.dumps({"schema_version": 1, "items": [{"id": "a"}]}),
    json.dumps({"schema_version": 1,
                "items": [{"id": "a", "text": "t", "extra": 1}]}),
    json.dumps({"schema_version": 1,
                "items": [{"id": "a", "text": "t"},
                          {"id": "a", "text": "u"}]}),
    json.dumps({"schema_version": 1,
                "items": [{"id": "", "text": "t"}]}),
    json.dumps({"schema_version": 1,
                "items": [{"id": "a", "text": 42}]}),
    json.dumps({"schema_version": 1, "items": "x"}),
    json.dumps({"schema_version": 1, "items": [{"id": "a", "text": "t"}],
                "hint": "nope"}),
])
def test_compress_malformed_payloads_fail_closed(payload):
    proc = _run_compress(payload)
    assert proc.returncode != 0
    assert proc.stdout == b""
    # Constant error text only — no payload echo, no exception details.
    assert proc.stderr == b"error: invalid compress request\n"


def test_compress_oversize_input_fails_closed():
    big = json.dumps({"schema_version": 1, "items": [
        {"id": "a", "text": "x" * (1024 * 1024)}]})
    proc = _run_compress(big)
    assert proc.returncode != 0
    assert proc.stdout == b""
    assert proc.stderr == b"error: invalid compress request\n"


def test_compress_too_many_items_fails_closed():
    items = [{"id": f"i{n}", "text": "t"} for n in range(129)]
    proc = _run_compress(json.dumps(
        {"schema_version": 1, "items": items}))
    assert proc.returncode != 0
    assert proc.stdout == b""


def test_compress_exactly_128_items_accepted():
    items = [{"id": f"i{n}", "text": "t"} for n in range(128)]
    out = _compress_ok(items)
    assert len(out["items"]) == 128


def test_compress_no_canary_echo_on_error():
    canary = "PRIV-CANARY-9f3e2a"
    proc = _run_compress(json.dumps({
        "schema_version": 1,
        "items": [{"id": canary, "text": canary, "bogus": canary}]}))
    assert proc.returncode != 0
    assert canary.encode() not in proc.stdout
    assert canary.encode() not in proc.stderr


# ---------------------------------------------------------------------------
# report helpers
# ---------------------------------------------------------------------------

_SEQ = iter(range(10_000))


def _ev(event_type: str, **kw) -> dict:
    row = {
        "schema_version": 1,
        "source": "pi",
        "mode": "observe",
        "run_id": "run-1",
        "project_id": "proj-1",
        "session_id": "sess-1",
        "event_type": event_type,
        "event_id": f"ev-{next(_SEQ)}",
        "timestamp_ms": 1_700_000_000_000 + next(_SEQ),
    }
    row.update(kw)
    return row


_UNSET = object()


def _usage(session="sess-1", message="m1", tokens=_UNSET, cost=None,
           stop="stop", **kw):
    if tokens is _UNSET:
        tokens = {"input": 10, "output": 5, "cache_read": 0,
                  "cache_write": 0, "total": 15}
    return _ev("model_usage", session_id=session, message_id=message,
               tokens=tokens, cost_estimate=cost, stop_reason=stop, **kw)


def _write(data_dir: Path, rows: list, name: str = "run-a.jsonl") -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / name
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(r if isinstance(r, str) else json.dumps(r))
            fh.write("\n")
    return path


def _report(data_dir: Path) -> dict:
    events, integrity = hp.iter_events(data_dir)
    return hp.build_report(events, integrity, data_dir)


def _session(report: dict, session_id: str = "sess-1") -> dict:
    for p in report["projects"]:
        for s in p["sessions"]:
            if s["session_id"] == session_id:
                return s
    raise AssertionError(f"session {session_id} not in report")


# ---------------------------------------------------------------------------
# report: happy path
# ---------------------------------------------------------------------------


def test_report_happy_path(tmp_path):
    _write(tmp_path, [
        _ev("collector_started", session_id=None,
            stats={"queue_max": 4096}),
        _ev("session_started"),
        _usage(cost=0.001),
        _ev("tool_result", tool_category="bash", is_error=False,
            call_id="c1"),
        _ev("tool_result", tool_category="read", is_error=True,
            call_id="c2"),
        _ev("context", input_bytes=5000, output_bytes=1200,
            changed_items=2, bridge_status="ok"),
        _ev("collector_stopped", session_id=None,
            stats={"written": 4, "dropped": 0}),
    ])
    rep = _report(tmp_path)
    t = rep["totals"]
    assert t["sessions"] == 1
    assert t["usage_messages"] == 1
    assert t["tokens"]["full"]["input"] == 10
    assert t["tokens"]["known_subtotal"]["total"] == 15
    assert t["cost_estimate"]["full"] == pytest.approx(0.001)
    assert t["tool_calls"]["bash"] == 1
    assert t["tool_calls"]["read"] == 1
    assert t["tool_errors"] == 1
    assert t["context"]["input_bytes"] == 5000
    assert t["context"]["bridge_status"]["ok"] == 1
    assert rep["integrity"]["rows_dropped"] == 0
    assert rep["runs"][0]["flags"] == []
    s = _session(rep)
    assert s["flags"] == []
    assert s["stop_reasons"] == {"stop": 1}


def test_report_compress_mode_rows_accepted(tmp_path):
    row = _ev("session_started")
    row["mode"] = "compress"
    _write(tmp_path, [row])
    rep = _report(tmp_path)
    assert rep["totals"]["events_by_mode"]["compress"] == 1


def test_report_json_output_parseable(tmp_path, capsys):
    _write(tmp_path, [_ev("session_started")])
    rc = hp.main(["report", "--data-dir", str(tmp_path), "--json"])
    assert rc == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["source"] == "pi"
    assert doc["totals"]["sessions"] == 1
    assert any("task-success" in c for c in doc["caveats"])


def _ctx(**kw):
    base = dict(input_bytes=100, output_bytes=40, changed_items=1,
                bridge_status="ok")
    base.update(kw)
    return _ev("context", **base)


def test_report_context_bridge_counters_totaled(tmp_path):
    _write(tmp_path, [
        _ev("session_started"),
        _usage(),
        _ctx(bridge_calls=3, cache_hits=2, cache_misses=1),
        _ctx(bridge_calls=1, cache_hits=1, cache_misses=0),
    ])
    rep = _report(tmp_path)
    br = rep["totals"]["context"]["bridge"]
    assert br["bridge_calls"]["known_subtotal"] == 4
    assert br["bridge_calls"]["full"] == 4
    assert br["cache_hits"]["full"] == 3
    assert br["cache_misses"]["full"] == 1
    assert br["bridge_calls"]["events_missing"] == 0


def test_report_context_counters_absent_old_rows_valid(tmp_path):
    """Old context rows without the counter fields stay valid; the
    'full' totals are null (unknown), never zero."""
    _write(tmp_path, [
        _ev("session_started"),
        _ctx(),
    ])
    rep = _report(tmp_path)
    assert rep["integrity"]["rows_dropped"] == 0
    br = rep["totals"]["context"]["bridge"]
    for k in ("bridge_calls", "cache_hits", "cache_misses"):
        assert br[k]["known_subtotal"] == 0
        assert br[k]["full"] is None
        assert br[k]["events_missing"] == 1


def test_report_context_counters_partial_missing_nulls_full(tmp_path):
    _write(tmp_path, [
        _ev("session_started"),
        _ctx(bridge_calls=2, cache_hits=1, cache_misses=1),
        _ctx(),  # second event omits the counters
    ])
    br = _report(tmp_path)["totals"]["context"]["bridge"]
    assert br["bridge_calls"]["known_subtotal"] == 2
    assert br["bridge_calls"]["full"] is None
    assert br["bridge_calls"]["events_missing"] == 1


@pytest.mark.parametrize("bad", [-1, 1.5, "3", True, float("nan")])
def test_report_bad_context_counter_drops_row(tmp_path, bad):
    _write(tmp_path, [
        _ev("session_started"),
        _ctx(bridge_calls=bad),
    ])
    rep = _report(tmp_path)
    assert rep["integrity"]["rows_dropped"] == 1
    assert rep["totals"]["context"]["events"] == 0


# ---------------------------------------------------------------------------
# report: usage dedup and missing/partial/nonfinite values
# ---------------------------------------------------------------------------


def test_report_duplicate_usage_deduped(tmp_path):
    _write(tmp_path, [
        _ev("session_started"),
        _usage(message="m1", cost=0.01),
        _usage(message="m1", cost=0.01),   # same message re-emitted
        _usage(message="m2", cost=0.02),
    ])
    rep = _report(tmp_path)
    assert rep["totals"]["usage_messages"] == 2
    assert rep["integrity"]["duplicate_usage_messages"] == 1
    assert rep["totals"]["cost_estimate"]["full"] == pytest.approx(0.03)


def test_report_usage_dedup_scoped_by_session(tmp_path):
    _write(tmp_path, [
        _usage(session="s1", message="m1"),
        _usage(session="s2", message="m1"),
    ])
    rep = _report(tmp_path)
    assert rep["totals"]["usage_messages"] == 2
    assert rep["integrity"]["duplicate_usage_messages"] == 0


def test_report_null_tokens_counted_missing_not_zero(tmp_path):
    _write(tmp_path, [
        _ev("session_started"),
        _usage(message="m1", tokens=None, cost=None),
        _usage(message="m2"),
    ])
    rep = _report(tmp_path)
    t = rep["totals"]
    assert t["usage_messages"] == 2
    assert t["usage_missing"] == 1
    # Full totals null while any contributing value is missing.
    assert t["tokens"]["full"] is None
    assert t["tokens"]["known_subtotal"]["input"] == 10
    assert t["cost_estimate"]["full"] is None
    assert t["cost_estimate"]["known_subtotal"] == 0.0
    assert t["cost_estimate"]["messages_missing"] == 2


def test_report_partial_tokens_subtotal_only(tmp_path):
    _write(tmp_path, [
        _usage(message="m1", tokens={"input": 7, "output": None,
                                     "cache_read": None,
                                     "cache_write": None,
                                     "total": None}),
    ])
    rep = _report(tmp_path)
    s = _session(rep)
    assert s["tokens"]["input"] == 7
    assert s["tokens_known"]["input"] == 1
    assert s["tokens_missing"]["output"] == 1
    assert rep["totals"]["tokens"]["full"] is None
    assert rep["totals"]["tokens"]["known_subtotal"]["input"] == 7


@pytest.mark.parametrize("bad", [float("nan"), float("inf"),
                                 float("-inf"), -1, "12", True])
def test_report_nonfinite_or_bad_tokens_drop_row(tmp_path, bad):
    _write(tmp_path, [
        _ev("session_started"),
        _usage(tokens={"input": bad, "output": 1, "cache_read": 0,
                       "cache_write": 0, "total": 1}),
    ])
    rep = _report(tmp_path)
    assert rep["integrity"]["rows_dropped"] == 1
    assert rep["totals"]["usage_messages"] == 0


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.5, "x"])
def test_report_bad_cost_drops_row(tmp_path, bad):
    _write(tmp_path, [_ev("session_started"), _usage(cost=bad)])
    rep = _report(tmp_path)
    assert rep["integrity"]["rows_dropped"] == 1
    assert rep["totals"]["usage_messages"] == 0


def test_report_zero_tokens_are_known_usage(tmp_path):
    _write(tmp_path, [
        _usage(tokens={"input": 0, "output": 0, "cache_read": 0,
                       "cache_write": 0, "total": 0}),
    ])
    rep = _report(tmp_path)
    s = _session(rep)
    assert s["usage_missing"] == 0
    assert all(v == 1 for v in s["tokens_known"].values())
    assert rep["totals"]["tokens"]["full"]["input"] == 0


def test_report_stop_reason_sanitized(tmp_path):
    _write(tmp_path, [
        _usage(message="m1", stop="toolUse"),
        _usage(message="m2", stop="provider/raw oddity sk-abc"),
        _usage(message="m3", stop=42),
        _usage(message="m4", stop=None),
    ])
    rep = _report(tmp_path)
    s = _session(rep)
    assert s["stop_reasons"] == {"toolUse": 1, "other": 3}
    assert rep["integrity"]["stop_reasons_coerced"] == 2
    assert rep["integrity"]["rows_dropped"] == 0


# ---------------------------------------------------------------------------
# report: collector-fidelity fixtures (rows shaped exactly as hive.ts emits)
# ---------------------------------------------------------------------------


def _collector_row(event_type: str, seq: int, **kw) -> dict:
    """A row in the exact envelope integrations/pi/hive.ts emits."""
    row = {
        "schema_version": 1,
        "source": "pi",
        "mode": "observe",
        "event_type": event_type,
        "event_id": f"e_{seq:016x}",
        "run_id": "r_0123456789abcdef01234567",
        "project_id": "a" * 32,
        "session_id": "b" * 32,
        "timestamp_ms": 1_700_000_000_000 + seq,
    }
    row.update(kw)
    return row


def test_report_collector_fidelity_run(tmp_path):
    """End-to-end rows exactly as the TS collector writes them:
    collector_started stats.queue_max, toolUse stop reasons, null
    stop_reason, collector_stopped stats.written/dropped."""
    rows = [
        _collector_row("collector_started", 1, session_id=None,
                       stats={"queue_max": 4096}),
        _collector_row("session_started", 2),
        _collector_row("model_usage", 3, message_id="c" * 32,
                       tokens={"input": 10, "output": 5,
                               "cache_read": 0, "cache_write": 0,
                               "total": 15},
                       cost_estimate=0.001, stop_reason="toolUse"),
        _collector_row("model_usage", 4, message_id="d" * 32,
                       tokens=None, cost_estimate=None,
                       stop_reason=None),
        _collector_row("tool_result", 5, call_id="e" * 32,
                       tool_category="bash", is_error=False),
        _collector_row("collector_stopped", 6, session_id=None,
                       stats={"written": 6, "dropped": 0}),
    ]
    _write(tmp_path, rows)
    rep = _report(tmp_path)
    assert rep["integrity"]["rows_dropped"] == 0
    assert rep["integrity"]["unknown_fields"] == 0
    assert rep["integrity"]["stop_reasons_coerced"] == 0
    run = rep["runs"][0]
    assert run["flags"] == []
    assert run["collector_stats"] == {"queue_max": 4096, "written": 6,
                                    "dropped": 0}
    s = _session(rep, session_id="b" * 32)
    assert s["stop_reasons"] == {"toolUse": 1, "other": 1}


def test_report_stop_reason_enum_matches_collector():
    """The reporter's accepted stop_reason vocabulary must contain every
    value the collector can emit verbatim (integrations/pi/hive.ts
    STOP_REASONS)."""
    src = (ROOT / "integrations" / "pi" / "hive.ts").read_text(
        encoding="utf-8")
    m = re.search(r"STOP_REASONS[^=]*=\s*\{([^}]*)\}", src)
    assert m, "STOP_REASONS not found in hive.ts"
    ts_reasons = set(re.findall(r"(\w+):\s*true", m.group(1)))
    assert ts_reasons
    assert ts_reasons <= hp.STOP_REASONS


# ---------------------------------------------------------------------------
# report: tool results, context, lifecycle flags
# ---------------------------------------------------------------------------


def test_report_tool_category_allowlist_enforced(tmp_path):
    _write(tmp_path, [
        _ev("session_started"),
        _ev("tool_result", tool_category="exfiltrate", is_error=False),
        _ev("tool_result", tool_category="bash", is_error=False),
    ])
    rep = _report(tmp_path)
    assert rep["integrity"]["rows_dropped"] == 1
    assert rep["totals"]["tool_calls"]["bash"] == 1


def test_report_tool_result_requires_bool_is_error(tmp_path):
    _write(tmp_path, [
        _ev("session_started"),
        _ev("tool_result", tool_category="bash", is_error="false"),
    ])
    rep = _report(tmp_path)
    assert rep["integrity"]["rows_dropped"] == 1
    assert rep["totals"]["tool_calls"]["bash"] == 0


def test_report_context_statuses_counted(tmp_path):
    _write(tmp_path, [
        _ev("session_started"),
        _ev("context", input_bytes=100, output_bytes=40,
            changed_items=1, bridge_status="ok"),
        _ev("context", input_bytes=200, output_bytes=200,
            changed_items=0, bridge_status="timeout"),
        _ev("context", input_bytes=50, output_bytes=50,
            changed_items=0, bridge_status="skipped"),
    ])
    rep = _report(tmp_path)
    ctx = rep["totals"]["context"]
    assert ctx["events"] == 3
    assert ctx["input_bytes"] == 350
    assert ctx["output_bytes"] == 290
    assert ctx["bridge_status"]["timeout"] == 1
    assert ctx["bridge_status"]["skipped"] == 1


def test_report_bad_bridge_status_drops_row(tmp_path):
    _write(tmp_path, [
        _ev("session_started"),
        _ev("context", input_bytes=1, output_bytes=1, changed_items=0,
            bridge_status="exploded"),
    ])
    rep = _report(tmp_path)
    assert rep["integrity"]["rows_dropped"] == 1


def test_report_missing_session_started_flagged(tmp_path):
    _write(tmp_path, [_usage()])
    s = _session(_report(tmp_path))
    assert "missing_session_started" in s["flags"]


def test_report_null_session_id_grouped_and_flagged(tmp_path):
    _write(tmp_path, [
        _ev("session_started", session_id=None),
        _usage(session=None, message="m1"),
    ])
    rep = _report(tmp_path)
    s = _session(rep, session_id=None)
    assert s["usage_messages"] == 1
    assert "session_id_unavailable" in s["flags"]


def test_report_collector_unclosed_flagged(tmp_path):
    _write(tmp_path, [
        _ev("collector_started", session_id=None,
            stats={"queue_max": 4096}),
        _ev("session_started"),
    ])
    rep = _report(tmp_path)
    run = rep["runs"][0]
    assert "collector_unclosed" in run["flags"]
    assert run["collector_stats"] == {"queue_max": 4096}


def test_report_collector_reported_drops_flagged(tmp_path):
    _write(tmp_path, [
        _ev("collector_started", session_id=None,
            stats={"queue_max": 4096}),
        _ev("session_started"),
        _ev("collector_stopped", session_id=None,
            stats={"written": 3, "dropped": 1}),
    ])
    rep = _report(tmp_path)
    run = rep["runs"][0]
    assert run["flags"] == ["collector_reported_drops"]
    assert run["collector_stats"] == {"queue_max": 4096, "written": 3,
                                    "dropped": 1}


def test_report_stats_keys_are_event_specific(tmp_path):
    """written/dropped on collector_started and queue_max on
    collector_stopped are off-contract: counted as unknown fields and
    never merged into collector_stats."""
    _write(tmp_path, [
        _ev("collector_started", session_id=None,
            stats={"queue_max": 4096, "written": 9, "dropped": 2}),
        _ev("session_started"),
        _ev("collector_stopped", session_id=None,
            stats={"written": 5, "dropped": 0, "queue_max": 1}),
    ])
    rep = _report(tmp_path)
    assert rep["integrity"]["unknown_fields"] == 3
    assert rep["runs"][0]["collector_stats"] == {"queue_max": 4096,
                                               "written": 5,
                                               "dropped": 0}


def test_report_duplicate_event_ids_deduped(tmp_path):
    ev = _ev("session_started")
    _write(tmp_path, [ev, dict(ev)])
    rep = _report(tmp_path)
    assert rep["integrity"]["duplicate_event_ids"] == 1
    assert rep["totals"]["sessions"] == 1


# ---------------------------------------------------------------------------
# report: fail-closed and strict behavior
# ---------------------------------------------------------------------------


def test_report_missing_dir_fails_closed(tmp_path):
    rc = hp.main(["report", "--data-dir", str(tmp_path / "nope")])
    assert rc == hp.EXIT_NO_EVIDENCE


def test_report_empty_dir_fails_closed(tmp_path, capsys):
    rc = hp.main(["report", "--data-dir", str(tmp_path)])
    assert rc == hp.EXIT_NO_EVIDENCE
    assert "no evidence" in capsys.readouterr().err


def test_report_all_malformed_fails_closed(tmp_path, capsys):
    _write(tmp_path, ["garbage", "{"])
    rc = hp.main(["report", "--data-dir", str(tmp_path)])
    assert rc == hp.EXIT_NO_EVIDENCE
    assert "no evidence" in capsys.readouterr().err


def test_report_partial_malformed_still_reports(tmp_path):
    _write(tmp_path, ["bad row",
                      _ev("session_started"),
                      _usage()])
    rep = _report(tmp_path)
    assert rep["integrity"]["rows_dropped"] == 1
    assert rep["totals"]["usage_messages"] == 1


def test_report_strict_exit_on_dropped_rows(tmp_path):
    _write(tmp_path, ["bad row", _ev("session_started")])
    assert hp.main(["report", "--data-dir", str(tmp_path),
                    "--strict"]) == hp.EXIT_INTEGRITY


def test_report_wrong_source_and_mode_dropped(tmp_path):
    _write(tmp_path, [
        _ev("session_started", source="opencode"),
        _ev("session_started", mode="train"),
        _ev("session_started"),
    ])
    rep = _report(tmp_path)
    assert rep["integrity"]["rows_dropped"] == 2
    assert rep["totals"]["sessions"] == 1


# ---------------------------------------------------------------------------
# report: privacy — unknown fields counted, never echoed
# ---------------------------------------------------------------------------


def test_report_no_canary_echo(tmp_path, capsys):
    canary = "PRIV-CANARY-7d1c9b"
    row = _ev("session_started")
    row["prompt_excerpt"] = canary          # unknown field
    bad = _ev("tool_result", tool_category="bash", is_error=False)
    bad["raw_output"] = canary              # unknown field
    _write(tmp_path, [row, bad,
                      json.dumps({"schema_version": 1, "source": "pi",
                                  "mode": "observe", "event_type": "nope",
                                  "event_id": "e", "run_id": "r",
                                  "project_id": "p",
                                  "timestamp_ms": 1,
                                  "blob": canary})])
    rc = hp.main(["report", "--data-dir", str(tmp_path)])
    assert rc == 0
    text_out = capsys.readouterr().out
    assert canary not in text_out
    rc = hp.main(["report", "--data-dir", str(tmp_path), "--json"])
    assert rc == 0
    json_out = capsys.readouterr().out
    assert canary not in json_out
    rep = json.loads(json_out)
    assert rep["integrity"]["unknown_fields"] == 2
    assert rep["integrity"]["rows_dropped"] == 1


def test_report_env_data_dir(tmp_path, monkeypatch, capsys):
    _write(tmp_path, [_ev("session_started")])
    monkeypatch.setenv(hp.ENV_DATA_DIR, str(tmp_path))
    rc = hp.main(["report"])
    assert rc == 0
    assert "sess-1" in capsys.readouterr().out


def test_compress_keeps_single_source_line_inside_test_log():
    text = _pytest_log() + "\nconst important = 'preserve me';\n===== 12 passed in 0.1s ====="
    assert hp.compress_items([{"id": "source", "text": text}])[0]["text"] == text


@pytest.mark.parametrize("field", ["mode", "event_type", "tool_category"])
def test_report_malformed_container_fields_are_dropped(tmp_path, field):
    row = _ev("tool_result", tool_category="bash", is_error=False)
    row[field] = []
    _write(tmp_path, [_ev("session_started"), row])
    assert _report(tmp_path)["integrity"]["rows_dropped"] == 1


def test_report_invalid_utf8_is_counted_not_raised(tmp_path):
    _write(tmp_path, [_ev("session_started")])
    (tmp_path / "invalid.jsonl").write_bytes(b"\xff")
    assert _report(tmp_path)["integrity"]["files_unreadable"] == 1


def test_report_unknown_collector_stats_are_not_echoed(tmp_path):
    canary = "PRIVATE_UNKNOWN_STATS_KEY"
    _write(tmp_path, [_ev("collector_stopped", stats={canary: 123})])
    assert canary not in json.dumps(_report(tmp_path))


def test_report_no_usage_does_not_claim_zero_full_usage(tmp_path):
    _write(tmp_path, [_ev("session_started")])
    totals = _report(tmp_path)["totals"]
    assert totals["tokens"]["full"] is None
    assert totals["cost_estimate"]["full"] is None


def test_report_incomplete_collector_keeps_subtotals_only(tmp_path):
    _write(tmp_path, [_ev("collector_started"), _ev("session_started"), _usage(cost=0.01)])
    totals = _report(tmp_path)["totals"]
    assert totals["tokens"]["known_subtotal"]["input"] > 0
    assert totals["tokens"]["full"] is None
    assert totals["cost_estimate"]["full"] is None
    assert hp.main(["report", "--data-dir", str(tmp_path), "--strict"]) == hp.EXIT_INTEGRITY


def test_compress_preserves_all_warnings_and_unknown_diagnostics():
    diagnostics = "\n".join(
        ["WARNING: dataset contains stale records"]
        + [f"DIAGNOSTIC: retained detail {i}" for i in range(45)])
    text = (_pytest_log() + "\n" + diagnostics
            + "\n===== 12 passed in 0.1s =====\n")
    item = hp.compress_items([{"id": "diagnostics", "text": text}])[0]
    assert item["label"] == "distill"
    assert len(item["text"].encode()) < len(text.encode())
    assert diagnostics in item["text"]
    assert "12 passed" in item["text"]
