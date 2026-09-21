"""Offline tests for scripts/external_bench.py — no model calls, no network.

The end-to-end test injects a fake backend that emits a scripted fix, so
the whole runner (manifest -> hash pin -> overlap guard -> rotating
conditions -> metered budgets -> atomic results/receipts -> fail-closed
report) is exercised without spending a token.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hive.llm import ModelResponse  # noqa: E402
from scripts import external_bench as eb  # noqa: E402
from scripts.external_bench import (  # noqa: E402
    BackendError,
    BudgetExceeded,
    ExternalBenchError,
    MeteredBackend,
    UsageUnknownError,
    condition_order,
    hash_task_source,
    load_manifest,
    load_training_inventory,
    run_suite,
)

# ---------------------------------------------------------------------------
# Fixtures: a miniature external suite on disk
# ---------------------------------------------------------------------------

FIX = "def budget(n):\n    return n // 3\n"


def _write_task(tasks_dir: Path, task_id: str) -> Path:
    """Author one held-out task in the hive_bench on-disk layout."""
    task_dir = tasks_dir / task_id
    repo = task_dir / "repo"
    (repo / "smoke").mkdir(parents=True)
    (repo / "svc").mkdir(parents=True)
    (repo / "svc" / "__init__.py").write_text("")
    (repo / "svc" / "core.py").write_text("def budget(n):\n    return n\n")
    (repo / "smoke" / "test_smoke.py").write_text(
        "from svc.core import budget\n\n\n"
        "def test_spec():\n    assert budget(3) == 1\n")
    (task_dir / "oracle" / "tests").mkdir(parents=True)
    (task_dir / "oracle" / "tests" / "test_hidden.py").write_text(
        "from svc.core import budget\n\n\ndef test_hidden():\n    assert budget(3) == 1\n")
    (task_dir / "task.json").write_text(json.dumps({
        "id": task_id, "family": "mini",
        "problem_statement": "budget must be shared",
        "test_cmd": "python -m pytest smoke -q",
        "oracle_cmd": "python -m pytest tests -q",
        "oracle_dir": "oracle",
    }))
    return task_dir


def _write_manifest(tmp_path: Path, task_ids=("mini",)) -> Path:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir(exist_ok=True)
    hashes = {tid: hash_task_source(_write_task(tasks_dir, tid))
              for tid in task_ids}
    manifest = {
        "schema_version": eb.SCHEMA_VERSION,
        "name": "mini-external",
        "tasks": list(task_ids),
        "source_sha256": hashes,
    }
    path = tasks_dir / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path


def _write_inventory(tmp_path: Path, *, task_ids=(), hashes=()) -> Path:
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps({"task_ids": list(task_ids),
                                "source_hashes": list(hashes)}))
    return path


class _FixBackend:
    """Scripted backend that writes the fix, verifies, then finishes.

    Reports real (non-zero) usage like a metered endpoint must. Stateless
    across episodes: the script position is the number of assistant turns
    already in the conversation, so a shared instance resets per episode.
    The extra ``finish`` covers the spec-review intercept: a held-out task
    with a write on disk gets one review turn before finishing.
    """

    _SCRIPT = ("write_file", "run_tests", "finish", "finish")

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, *, max_tokens=0, temperature=0.0, tools=None,
             tool_choice=None):
        step = sum(1 for m in messages if m["role"] == "assistant")
        tool = self._SCRIPT[min(step, len(self._SCRIPT) - 1)]
        self.calls += 1
        if tool == "write_file":
            text = ("ACTION: write_file\nPATH: svc/core.py\nCONTENT:\n"
                    f"```\n{FIX}```")
        else:
            text = f"ACTION: {tool}"
        return ModelResponse(text=text, prompt_tokens=100 + self.calls,
                             completion_tokens=10, duration_s=0.0,
                             model="fake-1", finish_reason="stop")


def _budgets(**over):
    base = {"max_requests": 500, "max_tokens_total": 1_000_000,
            "wall_seconds": 3600.0}
    base.update(over)
    return base


def _run_kwargs(tmp_path, manifest_path, inventory_path, backend, **over):
    tasks, manifest_info = load_manifest(manifest_path)
    kw = dict(
        tasks=tasks, manifest_info=manifest_info,
        inventory_info={"path": str(inventory_path), "sha256": "x",
                        "task_ids": 0, "source_hashes": 0},
        backend=backend, conditions=list(eb.CONDITIONS), repeat=1,
        policy_path=None, budgets=_budgets(),
        out_dir=tmp_path / "out", max_turns=25, max_tokens=4096,
        temperature=0.0, price_in=0.22, price_out=0.66,
        model="fake-1", endpoint="http://fake",
    )
    kw.update(over)
    return kw


# ---------------------------------------------------------------------------
# Manifest: strict, versioned, hash-pinned
# ---------------------------------------------------------------------------


def test_manifest_roundtrip_and_hash_pin(tmp_path):
    manifest = _write_manifest(tmp_path)
    tasks, info = load_manifest(manifest)
    assert [t.id for t in tasks] == ["mini"]
    assert tasks[0].oracle_dir is not None
    assert info["sha256"] and len(info["sha256"]) == 64
    eb.verify_source_hashes(tasks, info)  # unchanged sources pass


def test_manifest_rejects_wrong_version_and_unknown_keys(tmp_path):
    manifest = _write_manifest(tmp_path)
    doc = json.loads(manifest.read_text())
    doc["schema_version"] = 99
    manifest.write_text(json.dumps(doc))
    with pytest.raises(ExternalBenchError, match="schema_version"):
        load_manifest(manifest)
    doc = json.loads((tmp_path / "tasks" / "manifest.json").read_text())
    doc["schema_version"] = eb.SCHEMA_VERSION
    doc["surprise"] = True
    manifest.write_text(json.dumps(doc))
    with pytest.raises(ExternalBenchError, match="unknown keys"):
        load_manifest(manifest)


def test_manifest_rejects_empty_and_duplicate_tasks(tmp_path):
    manifest = _write_manifest(tmp_path)
    doc = json.loads(manifest.read_text())
    doc["tasks"] = []
    doc["source_sha256"] = {}
    manifest.write_text(json.dumps(doc))
    with pytest.raises(ExternalBenchError, match="empty"):
        load_manifest(manifest)
    doc = json.loads(manifest.read_text())
    doc["tasks"] = ["mini", "mini"]
    manifest.write_text(json.dumps(doc))
    with pytest.raises(ExternalBenchError, match="duplicate"):
        load_manifest(manifest)


def test_manifest_rejects_hash_map_mismatch(tmp_path):
    manifest = _write_manifest(tmp_path, ("mini", "mini2"))
    doc = json.loads(manifest.read_text())
    del doc["source_sha256"]["mini2"]
    manifest.write_text(json.dumps(doc))
    with pytest.raises(ExternalBenchError, match="cover exactly"):
        load_manifest(manifest)


def test_source_tamper_is_rejected(tmp_path):
    manifest = _write_manifest(tmp_path)
    tasks, info = load_manifest(manifest)
    # Any change under the task dir — repo, oracle, or task.json — must
    # flip the pinned hash.
    (tasks[0].repo_dir / "svc" / "core.py").write_text("def budget(n):\n    return 0\n")
    with pytest.raises(ExternalBenchError, match="source changed"):
        eb.verify_source_hashes(tasks, info)


def test_oracle_tamper_is_rejected(tmp_path):
    manifest = _write_manifest(tmp_path)
    tasks, info = load_manifest(manifest)
    (tasks[0].oracle_dir / "tests" / "test_hidden.py").write_text(
        "def test_hidden():\n    assert True\n")
    with pytest.raises(ExternalBenchError, match="source changed"):
        eb.verify_source_hashes(tasks, info)


# ---------------------------------------------------------------------------
# Training-overlap guard
# ---------------------------------------------------------------------------


def test_inventory_rejects_missing_malformed_and_empty(tmp_path):
    with pytest.raises(ExternalBenchError, match="unreadable"):
        load_training_inventory(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("[1,2]")
    with pytest.raises(ExternalBenchError, match="JSON object"):
        load_training_inventory(bad)
    empty = _write_inventory(tmp_path)
    with pytest.raises(ExternalBenchError, match="empty"):
        load_training_inventory(empty)


def test_overlap_on_task_id_fails(tmp_path):
    manifest = _write_manifest(tmp_path)
    _, info = load_manifest(manifest)
    inv = load_training_inventory(_write_inventory(tmp_path, task_ids=["mini"]))
    with pytest.raises(ExternalBenchError, match="overlaps"):
        eb.check_overlap(info, inv)


def test_overlap_on_source_hash_fails(tmp_path):
    manifest = _write_manifest(tmp_path)
    _, info = load_manifest(manifest)
    digest = next(iter(info["source_sha256"].values()))
    inv = load_training_inventory(_write_inventory(tmp_path, hashes=[digest]))
    with pytest.raises(ExternalBenchError, match="overlaps"):
        eb.check_overlap(info, inv)


def test_disjoint_inventory_passes(tmp_path):
    manifest = _write_manifest(tmp_path)
    _, info = load_manifest(manifest)
    inv = load_training_inventory(
        _write_inventory(tmp_path, task_ids=["other"], hashes=["0" * 64]))
    eb.check_overlap(info, inv)  # no raise


# ---------------------------------------------------------------------------
# Condition ordering
# ---------------------------------------------------------------------------


def test_condition_order_rotates_deterministically():
    conds = list(eb.CONDITIONS)
    orders = [condition_order(i, conds) for i in range(3)]
    assert orders[0] == conds
    assert orders[1] == conds[1:] + conds[:1]
    assert orders[2] == conds[2:] + conds[:2]
    assert orders[0] == condition_order(0, conds)  # deterministic
    assert all(sorted(o) == sorted(conds) for o in orders)


# ---------------------------------------------------------------------------
# Metered backend: budgets, usage, receipts
# ---------------------------------------------------------------------------


def _sink():
    rows = []
    return rows, rows.append


def test_metered_backend_counts_and_receipts():
    rows, sink = _sink()
    meter = MeteredBackend(_FixBackend(), max_requests=10,
                           max_tokens_total=10_000, wall_seconds=60,
                           receipt_sink=sink)
    meter.set_context(task_id="t1", pass_idx=0, condition="model-only")
    resp = meter.chat([{"role": "user", "content": "hi"}])
    assert resp.model == "fake-1"
    assert meter.requests == 1 and meter.prompt_tokens == 101
    assert rows[0]["task_id"] == "t1" and rows[0]["condition"] == "model-only"
    assert rows[0]["model"] == "fake-1" and rows[0]["ok"] is True


def test_request_budget_is_a_baseexception_abort():
    _rows, sink = _sink()
    meter = MeteredBackend(_FixBackend(), max_requests=1,
                           max_tokens_total=10_000, wall_seconds=60,
                           receipt_sink=sink)
    meter.chat([{"role": "user", "content": "hi"}])
    with pytest.raises(BudgetExceeded):
        meter.chat([{"role": "user", "content": "hi"}])
    # The abort must evade `except Exception` — that is what keeps
    # chat_with_retry from retrying an exhausted budget into hidden spend.
    try:
        meter.chat([{"role": "user", "content": "hi"}])
    except Exception:
        pytest.fail("BudgetExceeded must not be an Exception")
    except BudgetExceeded:
        pass
    assert meter.requests == 1  # refused calls are not billed


def test_token_budget_aborts():
    _rows, sink = _sink()
    meter = MeteredBackend(_FixBackend(), max_requests=10,
                           max_tokens_total=100, wall_seconds=60,
                           receipt_sink=sink)
    with pytest.raises(BudgetExceeded):
        meter.chat([{"role": "user", "content": "hi"}])  # 111 tokens > 100


def test_wall_budget_aborts():
    _rows, sink = _sink()
    meter = MeteredBackend(_FixBackend(), max_requests=10,
                           max_tokens_total=10_000, wall_seconds=0.001,
                           receipt_sink=sink)
    import time as _t
    _t.sleep(0.01)
    with pytest.raises(BudgetExceeded, match="wall"):
        meter.chat([{"role": "user", "content": "hi"}])


def test_unknown_usage_aborts():
    class _NoUsage:
        def chat(self, messages, **kw):
            return ModelResponse(text="ACTION: finish", prompt_tokens=0,
                                 completion_tokens=0, duration_s=0.0,
                                 model="m", finish_reason="stop")

    rows, sink = _sink()
    meter = MeteredBackend(_NoUsage(), max_requests=10,
                           max_tokens_total=10_000, wall_seconds=60,
                           receipt_sink=sink)
    with pytest.raises(UsageUnknownError):
        meter.chat([{"role": "user", "content": "hi"}])
    assert rows[0]["ok"] is False and "usage" in rows[0]["error"]


def test_backend_error_aborts_and_is_receipted():
    class _Boom:
        def chat(self, messages, **kw):
            raise RuntimeError("endpoint exploded")

    rows, sink = _sink()
    meter = MeteredBackend(_Boom(), max_requests=10,
                           max_tokens_total=10_000, wall_seconds=60,
                           receipt_sink=sink)
    with pytest.raises(BackendError):
        meter.chat([{"role": "user", "content": "hi"}])
    assert rows[0]["ok"] is False and "endpoint exploded" in rows[0]["error"]


# ---------------------------------------------------------------------------
# Row audit: fail closed
# ---------------------------------------------------------------------------


def _valid_row(task_id="mini", pass_idx=0, condition="model-only"):
    return {"task_id": task_id, "pass_idx": pass_idx, "condition": condition,
            "resolved": True, "crashed": False, "usage_recorded": True,
            "prompt_tokens": 1, "completion_tokens": 1, "llm_calls": 1,
            "turns": 1, "wall_clock_s": 0.1}


def test_audit_flags_duplicate_unmatched_missing_and_crashed(tmp_path):
    manifest = _write_manifest(tmp_path)
    tasks, _info = load_manifest(manifest)
    conds = ["model-only"]
    good = _valid_row()
    assert eb._audit_rows([good], tasks, conds, 1) == []
    dup = eb._audit_rows([good, dict(good)], tasks, conds, 1)
    assert any("duplicate" in p for p in dup)
    unmatched = eb._audit_rows([_valid_row(task_id="ghost")], tasks, conds, 1)
    assert any("unmatched" in p for p in unmatched)
    missing = eb._audit_rows([], tasks, conds, 1)
    assert any("missing" in p for p in missing)
    crashed = dict(good, crashed=True, usage_recorded=False)
    assert any("crashed" in p for p in eb._audit_rows([crashed], tasks, conds, 1))
    no_usage = dict(good, usage_recorded=False)
    assert eb._audit_rows([no_usage], tasks, conds, 1)


# ---------------------------------------------------------------------------
# Summaries: priced cost, null on zero success / unmeasured rows
# ---------------------------------------------------------------------------


def test_summarize_prices_cost_and_nulls_per_resolved_at_zero():
    rows = [_valid_row(), dict(_valid_row(), resolved=False)]
    s = eb.summarize_condition(rows, "model-only", price_in=1.0, price_out=1.0)
    assert s["resolved"] == 1 and s["usd_estimated"] == pytest.approx(4e-6)
    assert s["usd_per_resolved"] == pytest.approx(4e-6)
    s0 = eb.summarize_condition([dict(_valid_row(), resolved=False)],
                                "model-only", price_in=1.0, price_out=1.0)
    assert s0["usd_per_resolved"] is None  # cost/0 is undefined, not free


def test_summarize_nulls_usage_when_any_row_unmeasured():
    rows = [_valid_row(), dict(_valid_row(), usage_recorded=False)]
    s = eb.summarize_condition(rows, "model-only", price_in=1.0, price_out=1.0)
    assert s["usd_estimated"] is None and s["prompt_tokens"] is None


def test_paired_grid_requires_every_condition():
    rows = [_valid_row(condition=c) for c in eb.CONDITIONS]
    paired = eb.paired_grid(rows, list(eb.CONDITIONS))
    assert paired["cells_paired"] == 1
    assert paired["tasks_fully_paired"] == 1
    assert paired["grid"] == [{"task_id": "mini", "pass_idx": 0,
                               "resolved": {c: True for c in eb.CONDITIONS}}]
    # A task missing one condition is not paired — the cell is reported
    # incomplete with the conditions that did produce a verdict.
    rows2 = [r for r in rows if r["condition"] != "hive-trained"]
    p2 = eb.paired_grid(rows2, list(eb.CONDITIONS))
    assert p2["cells_paired"] == 0 and p2["cells_incomplete"] == 1
    assert p2["incomplete_cells"][0]["missing_conditions"] == ["hive-trained"]


def test_paired_grid_never_pairs_across_passes():
    """A partial run that recorded baseline pass 0 and hive pass 1 must
    not read as a paired comparison — pass identity is part of the cell."""
    rows = [_valid_row(pass_idx=0, condition="model-only"),
            _valid_row(pass_idx=1, condition="hive-rule")]
    paired = eb.paired_grid(rows, ["model-only", "hive-rule"])
    assert paired["cells_paired"] == 0
    assert paired["tasks_fully_paired"] == 0
    # Both cells are reported, each honestly incomplete.
    assert paired["cells_incomplete"] == 2
    by_pass = {c["pass_idx"]: c for c in paired["incomplete_cells"]}
    assert by_pass[0]["resolved"] == {"model-only": True}
    assert by_pass[0]["missing_conditions"] == ["hive-rule"]
    assert by_pass[1]["resolved"] == {"hive-rule": True}
    assert by_pass[1]["missing_conditions"] == ["model-only"]


def test_paired_grid_rejects_duplicate_rows_as_ambiguous():
    """Two valid rows for the same (task, pass, condition) can disagree;
    collapsing them into a pairing would pick a winner silently."""
    rows = [_valid_row(condition=c) for c in eb.CONDITIONS]
    rows.append(dict(_valid_row(condition="hive-rule"), resolved=False))
    paired = eb.paired_grid(rows, list(eb.CONDITIONS))
    assert paired["cells_paired"] == 0
    assert paired["cells_ambiguous"] == 1
    amb = paired["ambiguous_cells"][0]
    assert amb["task_id"] == "mini" and amb["pass_idx"] == 0
    assert amb["duplicated_conditions"] == ["hive-rule"]
    assert amb["resolved"]["hive-rule"] == [True, False]


def test_paired_grid_marks_crashed_condition_incomplete():
    """A crashed episode is an observed-but-invalid row: the cell is
    incomplete, not silently absent and not paired."""
    rows = [_valid_row(condition=c) for c in eb.CONDITIONS]
    rows[1] = dict(rows[1], crashed=True, resolved=False,
                   usage_recorded=False)
    paired = eb.paired_grid(rows, list(eb.CONDITIONS))
    assert paired["cells_paired"] == 0
    inc = paired["incomplete_cells"][0]
    assert inc["invalid_conditions"] == ["hive-rule"]
    assert inc["missing_conditions"] == []

def test_paired_grid_mixed_validity_duplicate_is_ambiguous():
    """A valid row plus a crashed/usage-less row for the same
    (task, pass, condition) is still a duplicate episode — the cell must
    not pair on the valid row alone."""
    rows = [_valid_row(condition=c) for c in eb.CONDITIONS]
    rows.append(dict(_valid_row(condition="hive-rule"), crashed=True,
                     resolved=False, usage_recorded=False))
    paired = eb.paired_grid(rows, list(eb.CONDITIONS))
    assert paired["cells_paired"] == 0
    assert paired["cells_ambiguous"] == 1
    amb = paired["ambiguous_cells"][0]
    assert amb["duplicated_conditions"] == ["hive-rule"]
    assert amb["rows"]["hive-rule"] == 2
    assert amb["resolved"]["hive-rule"] == [True]


def test_paired_grid_absent_pass_is_incomplete_not_fully_paired():
    """With repeat=2 planned and only pass 0 rows recorded, the task must
    not report fully paired — the absent pass is an incomplete cell."""
    rows = [_valid_row(condition=c) for c in eb.CONDITIONS]
    expected = {("mini", 0), ("mini", 1)}
    paired = eb.paired_grid(rows, list(eb.CONDITIONS), expected=expected)
    assert paired["cells_paired"] == 1
    assert paired["cells_incomplete"] == 1
    assert paired["tasks_fully_paired"] == 0
    assert paired["tasks_all_conditions_resolved"] == 0
    absent = paired["incomplete_cells"][0]
    assert absent["task_id"] == "mini" and absent["pass_idx"] == 1
    assert absent["missing_conditions"] == list(eb.CONDITIONS)
    assert absent["resolved"] == {}


def test_paired_grid_task_metrics_are_task_level_at_repeat_2():
    """One task fully paired and resolved over two passes counts once at
    task level, not once per cell."""
    rows = [_valid_row(pass_idx=p, condition=c)
            for p in (0, 1) for c in eb.CONDITIONS]
    expected = {("mini", 0), ("mini", 1)}
    paired = eb.paired_grid(rows, list(eb.CONDITIONS), expected=expected)
    assert paired["cells_paired"] == 2
    assert paired["tasks_fully_paired"] == 1
    assert paired["tasks_all_conditions_resolved"] == 1


def test_paired_grid_ignores_rows_outside_expected_plan():
    """An unplanned (task, pass) cell — even a complete-looking one — is
    excluded when the planned universe is supplied; the audit reports it
    as unmatched instead of the grid inflating task counts."""
    rows = [_valid_row(condition=c) for c in eb.CONDITIONS]
    rows += [_valid_row(task_id="rogue", condition=c)
             for c in eb.CONDITIONS]
    paired = eb.paired_grid(rows, list(eb.CONDITIONS),
                            expected={("mini", 0)})
    assert paired["cells_paired"] == 1
    assert paired["tasks_fully_paired"] == 1
    assert all(c["task_id"] == "mini" for c in paired["grid"])


# ---------------------------------------------------------------------------
# End-to-end: injected fake backend, no model calls
# ---------------------------------------------------------------------------


def test_run_suite_end_to_end_offline(tmp_path, monkeypatch):
    manifest = _write_manifest(tmp_path)
    inventory = _write_inventory(tmp_path, task_ids=["trained-task"],
                                 hashes=["f" * 64])
    # hive-trained needs a policy; stub the signature-guarded loader so the
    # offline test never touches joblib.
    from hive.harness import RuleBasedRoutingPolicy

    monkeypatch.setattr(eb, "load_trained_policy",
                        lambda path: RuleBasedRoutingPolicy())
    report = run_suite(**_run_kwargs(tmp_path, manifest, inventory,
                                     _FixBackend()))
    assert report["status"] == "complete", report["audit_problems"]
    assert report["promotable"] is True
    out = tmp_path / "out"
    # Atomic artifacts exist and agree with the report.
    result_lines = (out / "results.jsonl").read_text().strip().splitlines()
    receipt_lines = (out / "receipts.jsonl").read_text().strip().splitlines()
    assert len(result_lines) == 3  # 1 task x 1 pass x 3 conditions
    assert json.loads((out / "report.json").read_text())["status"] == "complete"
    # Every receipt names task/pass/condition and the actual model.
    receipts = [json.loads(ln) for ln in receipt_lines]
    assert all(r["task_id"] == "mini" and r["model"] == "fake-1"
               and r["condition"] in eb.CONDITIONS for r in receipts)
    # Receipts are the true request count; llm_calls rows must sum to it.
    rows = [json.loads(ln) for ln in result_lines]
    assert len(receipts) == sum(r["llm_calls"] for r in rows)
    # The fake writes the fix, so all three conditions resolve and the
    # paired grid is complete.
    assert report["paired"]["cells_paired"] == 1
    assert report["paired"]["tasks_fully_paired"] == 1
    assert report["paired"]["tasks_all_conditions_resolved"] == 1
    assert report["paired"]["incomplete_cells"] == []
    assert report["paired"]["ambiguous_cells"] == []
    for c in eb.CONDITIONS:
        assert report["summaries"][c]["resolved"] == 1
        assert report["summaries"][c]["usd_per_resolved"] is not None
    # Fresh workdir per episode (plus its pristine -grade rebuild).
    workdirs = [d for d in (out / "workdirs").iterdir()
                if not d.name.endswith("-grade")]
    assert len(workdirs) == 3


def test_budget_abort_preserves_partial_run(tmp_path, monkeypatch):
    manifest = _write_manifest(tmp_path)
    inventory = _write_inventory(tmp_path, task_ids=["x"], hashes=["0" * 64])
    monkeypatch.setattr(eb, "load_trained_policy",
                        lambda path: object())
    kw = _run_kwargs(tmp_path, manifest, inventory, _FixBackend(),
                     budgets=_budgets(max_requests=1))
    report = run_suite(**kw)
    assert report["status"] == "aborted"
    assert report["promotable"] is False
    assert "BudgetExceeded" in report["abort_reason"]
    out = tmp_path / "out"
    # The partial run is preserved: rows and receipts on disk, report
    # written, nothing promoted.
    assert (out / "results.jsonl").read_text().strip()
    assert (out / "receipts.jsonl").read_text().strip()
    assert json.loads((out / "report.json").read_text())["status"] == "aborted"


def test_backend_error_aborts_run(tmp_path, monkeypatch):
    class _Boom:
        def chat(self, messages, **kw):
            raise RuntimeError("nope")

    manifest = _write_manifest(tmp_path)
    inventory = _write_inventory(tmp_path, task_ids=["x"], hashes=["0" * 64])
    monkeypatch.setattr(eb, "load_trained_policy", lambda path: object())
    report = run_suite(**_run_kwargs(tmp_path, manifest, inventory, _Boom()))
    assert report["status"] == "aborted"
    assert "BackendError" in report["abort_reason"]
    assert report["usage_totals"]["prompt_tokens"] is None
    assert report["usage_totals"]["completion_tokens"] is None
    assert report["usage_totals"]["requests"] == 1
    assert report["usage_totals"]["usage_recorded"] is False


def test_output_dir_must_be_new(tmp_path):
    manifest = _write_manifest(tmp_path)
    inventory = _write_inventory(tmp_path, task_ids=["x"], hashes=["0" * 64])
    (tmp_path / "out").mkdir()
    kw = _run_kwargs(tmp_path, manifest, inventory, _FixBackend())
    with pytest.raises(FileExistsError):
        run_suite(**kw)


# ---------------------------------------------------------------------------
# dry-run: full validation, zero model calls
# ---------------------------------------------------------------------------


def test_dry_run_validates_without_backend(tmp_path, capsys, monkeypatch):
    manifest = _write_manifest(tmp_path)
    inventory = _write_inventory(tmp_path, task_ids=["x"], hashes=["0" * 64])
    called = []
    monkeypatch.setattr(eb, "load_trained_policy",
                        lambda path: called.append(path) or object())
    rc = eb.dry_run(manifest_path=manifest, inventory_path=inventory,
                    conditions=["model-only", "hive-rule"], repeat=1,
                    policy_path=None)
    assert rc == 0
    out = capsys.readouterr().out
    assert "no model calls" in out and "no overlap" in out
    assert called == []  # hive-trained not selected: policy never loaded


def test_dry_run_fails_on_overlap(tmp_path):
    manifest = _write_manifest(tmp_path)
    inventory = _write_inventory(tmp_path, task_ids=["mini"])
    with pytest.raises(ExternalBenchError, match="overlaps"):
        eb.dry_run(manifest_path=manifest, inventory_path=inventory,
                   conditions=["model-only"], repeat=1, policy_path=None)


def test_trained_condition_requires_policy_path(tmp_path):
    manifest = _write_manifest(tmp_path)
    inventory = _write_inventory(tmp_path, task_ids=["x"], hashes=["0" * 64])
    with pytest.raises(ExternalBenchError, match="policy-path"):
        eb.validate_setup(manifest_path=manifest, inventory_path=inventory,
                          conditions=list(eb.CONDITIONS), policy_path=None,
                          check_policy=False)


# ---------------------------------------------------------------------------
# Guard hardening: reservation, timeout clamp, input validation, isolation
# ---------------------------------------------------------------------------


def test_precall_token_reservation_blocks_overspend():
    """A call that could overspend the token budget is refused before the
    backend is invoked — no hidden spend."""
    backend = _FixBackend()
    _rows, sink = _sink()
    meter = MeteredBackend(backend, max_requests=10,
                           max_tokens_total=200, wall_seconds=60,
                           receipt_sink=sink)
    with pytest.raises(BudgetExceeded, match="reserve"):
        meter.chat([{"role": "user", "content": "hi"}],
                   max_tokens=4096)
    assert backend.calls == 0 and meter.requests == 0


def test_backend_timeout_clamped_to_wall_budget():
    backend = _FixBackend()
    backend.timeout = 300.0  # pretend HTTP backend attribute
    _rows, sink = _sink()
    meter = MeteredBackend(backend, max_requests=10,
                           max_tokens_total=10**9, wall_seconds=60,
                           receipt_sink=sink)
    meter.chat([{"role": "user", "content": "hi"}], max_tokens=10)
    assert 0 < backend.timeout <= 60.0


def test_meter_rejects_invalid_budgets():
    _rows, sink = _sink()
    for bad in ({"max_requests": 0}, {"max_tokens_total": -1},
                {"wall_seconds": 0}, {"wall_seconds": float("nan")},
                {"wall_seconds": float("inf")}):
        kw = _budgets()
        kw.update(bad)
        with pytest.raises(ExternalBenchError):
            MeteredBackend(_FixBackend(), max_requests=kw["max_requests"],
                           max_tokens_total=kw["max_tokens_total"],
                           wall_seconds=kw["wall_seconds"], receipt_sink=sink)


def test_row_valid_rejects_malformed_counters():
    good = _valid_row()
    assert eb._row_valid(good)
    for bad in (dict(good, prompt_tokens=True),
                dict(good, prompt_tokens=-1),
                dict(good, llm_calls=1.5),
                dict(good, wall_clock_s=float("nan")),
                dict(good, wall_clock_s=-0.5),
                dict(good, turns="3")):
        assert not eb._row_valid(bad), bad


def test_audit_fails_closed_on_empty_plan(tmp_path):
    manifest = _write_manifest(tmp_path)
    tasks, _info = load_manifest(manifest)
    assert eb._audit_rows([], tasks, [], 1)  # no conditions -> no plan
    assert eb._audit_rows([], [], ["model-only"], 1)  # no tasks -> no plan


def test_validate_setup_rejects_bad_conditions(tmp_path):
    manifest = _write_manifest(tmp_path)
    inventory = _write_inventory(tmp_path, task_ids=["x"], hashes=["0" * 64])
    for conds in ([], ["bogus"], ["model-only", "model-only"]):
        with pytest.raises(ExternalBenchError):
            eb.validate_setup(manifest_path=manifest,
                              inventory_path=inventory, conditions=conds,
                              policy_path=None, check_policy=False)


def test_inventory_rejects_blank_ids_and_bad_hashes(tmp_path):
    with pytest.raises(ExternalBenchError, match="safe ids"):
        load_training_inventory(_write_inventory(tmp_path, task_ids=[" "]))
    with pytest.raises(ExternalBenchError, match="sha256"):
        load_training_inventory(_write_inventory(tmp_path, hashes=["garbage"]))


def test_manifest_rejects_traversal_and_id_mismatch(tmp_path):
    manifest = _write_manifest(tmp_path)
    doc = json.loads(manifest.read_text())
    doc["tasks"] = ["../escape"]
    doc["source_sha256"] = {"../escape": "a" * 64}
    manifest.write_text(json.dumps(doc))
    with pytest.raises(ExternalBenchError, match="path components"):
        load_manifest(manifest)
    # task.json id must match the manifest id
    (tmp_path / "m2").mkdir()
    manifest = _write_manifest(tmp_path / "m2")
    task_json = tmp_path / "m2" / "tasks" / "mini" / "task.json"
    meta = json.loads(task_json.read_text())
    meta["id"] = "renamed"
    task_json.write_text(json.dumps(meta))
    with pytest.raises(ExternalBenchError, match="declares id"):
        load_manifest(manifest)

def test_manifest_rejects_oracle_outside_task_dir(tmp_path):
    manifest = _write_manifest(tmp_path)
    task_json = tmp_path / "tasks" / "mini" / "task.json"
    meta = json.loads(task_json.read_text())
    meta["oracle_dir"] = "../shared-oracle"
    task_json.write_text(json.dumps(meta))
    # task.json changed -> hash pin also fails, but the containment check
    # must fire first (it runs before hashing in load_manifest).
    with pytest.raises(ExternalBenchError, match="escapes"):
        load_manifest(manifest)


def test_task_tree_symlink_rejected(tmp_path):
    manifest = _write_manifest(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n")
    (tmp_path / "tasks" / "mini" / "repo" / "link.py").symlink_to(outside)
    tasks, info = load_manifest(manifest)
    with pytest.raises(ExternalBenchError, match="symlink"):
        eb.verify_source_hashes(tasks, info)


def test_source_hash_is_unambiguous_across_file_boundaries(tmp_path):
    """path\\0content\\0 concatenation would collide: one file 'a' holding
    'x\\0b\\0y' must hash differently from files 'a'='x' and 'b'='y'."""
    one = tmp_path / "one"
    one.mkdir()
    (one / "a").write_bytes(b"x\x00b\x00y")
    two = tmp_path / "two"
    two.mkdir()
    (two / "a").write_bytes(b"x")
    (two / "b").write_bytes(b"y")
    assert hash_task_source(one) != hash_task_source(two)

def test_episode_policies_are_fresh_copies(tmp_path, monkeypatch):
    """build_stack must deep-copy the policy: CPURouterPolicy.predict
    stores _last_route, so a shared object leaks routing state between
    episodes even with a fresh brain."""
    from hive.harness import RuleBasedRoutingPolicy

    template = RuleBasedRoutingPolicy()
    template.stats["routed"] = 7  # pretend prior-episode state
    s1 = eb.build_stack("hive-rule", rule_policy=template, trained_policy=None)
    s2 = eb.build_stack("hive-rule", rule_policy=template, trained_policy=None)
    assert s1.busybee is not s2.busybee
    assert s1.busybee is not template
    assert s1.busybee.stats["routed"] == 7  # copy sees template state…
    s1.busybee.stats["routed"] = 99
    assert template.stats["routed"] == 7    # …but the template is untouched
    assert s2.busybee.stats["routed"] == 7


def test_mid_run_source_change_aborts(tmp_path, monkeypatch):
    """A source that mutates between episodes aborts the run and still
    writes report.json — never reports complete on tampered input."""
    manifest = _write_manifest(tmp_path)
    inventory = _write_inventory(tmp_path, task_ids=["x"], hashes=["0" * 64])
    monkeypatch.setattr(eb, "load_trained_policy", lambda path: object())
    target = tmp_path / "tasks" / "mini" / "repo" / "svc" / "core.py"
    original = target.read_text()
    calls = {"n": 0}
    real_verify = eb.verify_source_hashes

    def tamper_after_first(tasks, info):
        real_verify(tasks, info)
        calls["n"] += 1
        if calls["n"] == 1:
            target.write_text("def budget(n):\n    return -1\n")

    monkeypatch.setattr(eb, "verify_source_hashes", tamper_after_first)
    try:
        report = run_suite(**_run_kwargs(tmp_path, manifest, inventory,
                                         _FixBackend()))
    finally:
        target.write_text(original)
    assert report["status"] == "aborted"
    assert "SourceChanged" in report["abort_reason"]
    assert json.loads((tmp_path / "out" / "report.json")
                      .read_text())["status"] == "aborted"


def test_unknown_call_preserves_subtotal_without_claiming_full_usage():
    class SometimesMissing:
        calls = 0

        def chat(self, messages, **kwargs):
            self.calls += 1
            return ModelResponse(
                text="ACTION: finish",
                prompt_tokens=100 if self.calls == 1 else 0,
                completion_tokens=10 if self.calls == 1 else 0,
                duration_s=0.1, model="fake", finish_reason="stop",
            )

    _rows, sink = _sink()
    meter = MeteredBackend(SometimesMissing(), max_requests=3,
                           max_tokens_total=10_000, wall_seconds=60,
                           receipt_sink=sink)
    meter.chat([{"role": "user", "content": "hi"}], max_tokens=20)
    with pytest.raises(UsageUnknownError):
        meter.chat([{"role": "user", "content": "hi"}], max_tokens=20)
    usage = meter.usage_summary()
    assert usage["requests"] == 2
    assert usage["metered_responses"] == 1
    assert usage["usage_recorded"] is False
    assert usage["prompt_tokens"] is None
    assert usage["completion_tokens"] is None
    assert usage["measured_subtotal"] == {
        "prompt_tokens": 100, "completion_tokens": 10,
    }


def test_budget_abort_does_not_erase_known_usage():
    _rows, sink = _sink()
    meter = MeteredBackend(_FixBackend(), max_requests=1,
                           max_tokens_total=10_000, wall_seconds=60,
                           receipt_sink=sink)
    meter.chat([{"role": "user", "content": "hi"}], max_tokens=20)
    with pytest.raises(BudgetExceeded):
        meter.chat([{"role": "user", "content": "hi"}], max_tokens=20)
    usage = meter.usage_summary()
    assert usage["usage_recorded"] is True
    assert usage["requests"] == usage["metered_responses"] == 1
    assert usage["prompt_tokens"] == meter.prompt_tokens
    assert usage["completion_tokens"] == meter.completion_tokens
