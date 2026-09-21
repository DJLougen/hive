"""Evidence-contract tests: trajectory rebuild exclusion/provenance, training
held-out guards, and claim-gate mutation coverage. No LLM calls, no network."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import check_claims, rebuild_trajectories, train_cpu_policy  # noqa: E402

# --------------------------------------------------------------------------- #
# Fixtures: a minimal bench artifact + suite manifest
# --------------------------------------------------------------------------- #


def _artifact(task_ids: list[str], *, tag_steps: bool = True) -> dict:
    """A two-episode-per-task artifact with a plausible step sequence."""
    results = []
    for tid in task_ids:
        for pass_idx in range(2):
            results.append({
                "task_id": tid if tag_steps else None,
                "arm": "hive",
                "pass_idx": pass_idx,
                "steps": [
                    {"tool": "list_files", "decision_source": "llm", "ok": True,
                     "args": {}},
                    {"tool": "run_tests", "decision_source": "llm", "ok": True,
                     "args": {}},
                    {"tool": "finish", "decision_source": "llm", "ok": True,
                     "args": {}},
                ],
            })
    return {"results": results}


def _write_artifact(path: Path, task_ids: list[str], **kw) -> Path:
    path.write_text(json.dumps(_artifact(task_ids, **kw)))
    return path


def _write_suite(path: Path, task_ids: list[str]) -> Path:
    path.write_text(json.dumps({"name": "t", "tasks": task_ids}))
    return path


# --------------------------------------------------------------------------- #
# rebuild_trajectories
# --------------------------------------------------------------------------- #


def test_rebuild_missing_source_fails_closed(tmp_path, capsys):
    out = tmp_path / "rows.jsonl"
    rc = rebuild_trajectories.main([
        "--out", str(out), "--source", str(tmp_path / "absent.json")])
    assert rc == 2
    assert not out.exists()
    assert "absent" in capsys.readouterr().err


def test_rebuild_exclude_suite_drops_tasks_and_writes_provenance(tmp_path):
    art = _write_artifact(tmp_path / "run.json", ["keep-a", "eval-b"])
    suite = _write_suite(tmp_path / "suite.eval.json", ["eval-b"])
    out = tmp_path / "rows.jsonl"

    rc = rebuild_trajectories.main([
        "--out", str(out),
        "--source", str(art),
        "--exclude-suite", str(suite)])
    assert rc == 0

    rows = [json.loads(ln) for ln in out.read_text().splitlines()]
    assert rows and {r["task"] for r in rows} == {"keep-a"}

    prov = json.loads((tmp_path / "rows.jsonl.provenance.json").read_text())
    assert prov["schema_version"] == "hive-trajectory-provenance/v1"
    assert prov["row_count"] == len(rows)
    assert prov["tasks"] == ["keep-a"]
    assert prov["excluded_tasks"] == ["eval-b"]
    assert prov["sources"][str(art)] == hashlib.sha256(art.read_bytes()).hexdigest()
    assert prov["corpus_sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()


def test_rebuild_exclude_suite_rejects_untagged_rows(tmp_path, capsys):
    art = _write_artifact(tmp_path / "run.json", ["keep-a"], tag_steps=False)
    suite = _write_suite(tmp_path / "suite.eval.json", ["eval-b"])
    out = tmp_path / "rows.jsonl"
    rc = rebuild_trajectories.main([
        "--out", str(out), "--source", str(art),
        "--exclude-suite", str(suite)])
    assert rc == 2
    assert not out.exists()
    assert "no task id" in capsys.readouterr().err


def test_rebuild_empty_corpus_fails(tmp_path, capsys):
    art = tmp_path / "run.json"
    art.write_text(json.dumps({"results": []}))
    out = tmp_path / "rows.jsonl"
    rc = rebuild_trajectories.main(["--out", str(out), "--source", str(art)])
    assert rc == 2
    assert not out.exists()
    assert "empty" in capsys.readouterr().err


def test_rebuild_exclude_everything_fails(tmp_path, capsys):
    art = _write_artifact(tmp_path / "run.json", ["eval-b"])
    suite = _write_suite(tmp_path / "suite.eval.json", ["eval-b"])
    out = tmp_path / "rows.jsonl"
    rc = rebuild_trajectories.main([
        "--out", str(out), "--source", str(art),
        "--exclude-suite", str(suite)])
    assert rc == 2
    assert not out.exists()


# --------------------------------------------------------------------------- #
# train_cpu_policy guards
# --------------------------------------------------------------------------- #


def _rows(task_ids: list[str], *, tagged: bool = True) -> str:
    lines = []
    for tid in task_ids:
        for tool in ("list_files", "run_tests", "finish"):
            row = {"state": {"listed": True, "tests_run": 1, "last_tool": "none"},
                   "tool": tool, "ok": True}
            if tagged:
                row["task"] = tid
            lines.append(json.dumps(row))
    return "\n".join(lines) + "\n"


def test_train_empty_corpus_fails(tmp_path, capsys):
    traj = tmp_path / "t.jsonl"
    traj.write_text("")
    rc = train_cpu_policy.main([
        "--trajectories", str(traj), "--out", str(tmp_path / "m.joblib")])
    assert rc == 2
    assert "empty" in capsys.readouterr().err


def test_train_eval_overlap_fails_closed(tmp_path, capsys):
    traj = tmp_path / "t.jsonl"
    traj.write_text(_rows(["eval-b", "keep-a"]))
    suite = _write_suite(tmp_path / "suite.eval.json", ["eval-b"])
    rc = train_cpu_policy.main([
        "--trajectories", str(traj), "--out", str(tmp_path / "m.joblib"),
        "--eval-suite", str(suite)])
    assert rc == 2
    assert "eval-b" in capsys.readouterr().err
    assert not (tmp_path / "m.joblib").exists()


def test_train_eval_overlap_requires_explicit_opt_in(tmp_path):
    pytest.importorskip("sklearn")
    traj = tmp_path / "t.jsonl"
    traj.write_text(_rows(["eval-b", "keep-a"]))
    suite = _write_suite(tmp_path / "suite.eval.json", ["eval-b"])
    out = tmp_path / "m.joblib"
    rc = train_cpu_policy.main([
        "--trajectories", str(traj), "--out", str(out),
        "--eval-suite", str(suite), "--allow-eval-overlap"])
    assert rc == 0
    assert out.exists()


def test_train_exclude_suite_removes_overlap(tmp_path):
    pytest.importorskip("sklearn")
    traj = tmp_path / "t.jsonl"
    traj.write_text(_rows(["eval-b", "keep-a"]))
    suite = _write_suite(tmp_path / "suite.eval.json", ["eval-b"])
    out = tmp_path / "m.joblib"
    rc = train_cpu_policy.main([
        "--trajectories", str(traj), "--out", str(out),
        "--exclude-suite", str(suite), "--eval-suite", str(suite)])
    assert rc == 0
    assert out.exists()


def test_train_untagged_rows_fail_when_guard_active(tmp_path, capsys):
    traj = tmp_path / "t.jsonl"
    traj.write_text(_rows(["keep-a"], tagged=False))
    suite = _write_suite(tmp_path / "suite.eval.json", ["eval-b"])
    rc = train_cpu_policy.main([
        "--trajectories", str(traj), "--out", str(tmp_path / "m.joblib"),
        "--eval-suite", str(suite)])
    assert rc == 2
    assert "no task id" in capsys.readouterr().err


def test_train_refuses_to_overwrite_existing_checkpoint(tmp_path, capsys):
    out = tmp_path / "m.joblib"
    out.write_bytes(b"committed")
    traj = tmp_path / "t.jsonl"
    traj.write_text(_rows(["keep-a"]))
    rc = train_cpu_policy.main(["--trajectories", str(traj), "--out", str(out)])
    assert rc == 2
    assert out.read_bytes() == b"committed"
    assert "--overwrite" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# check_claims mutation coverage
# --------------------------------------------------------------------------- #


def _fake_artifact() -> dict:
    return {
        "summary": {
            "baseline": {"resolved": 77, "tasks": 90, "resolve_rate": 0.8556,
                         "mean_llm_calls": 7.31,
                         "usd_total": 0.368,
                         "per_task_resolved": {"task-x": "15/15"}},
            "context": {"resolved": 74, "tasks": 90, "resolve_rate": 0.8222,
                        "mean_llm_calls": 7.20,
                        "usd_total": 0.394,
                        "per_task_resolved": {"task-x": "13/15"}},
            "hive": {"resolved": 74, "tasks": 90, "resolve_rate": 0.8222,
                     "mean_llm_calls": 3.04,
                     "usd_total": 0.214,
                     "per_task_resolved": {"task-x": "10/15"}},
            "comparisons": {"baseline_vs_hive": {"p": 1.0,
                                               "verdict": "not_separable"}},
        },
        "results": [
            {"arm": "baseline", "task_id": "task-x", "resolved": True},
            {"arm": "baseline", "task_id": "task-x", "resolved": True},
            {"arm": "hive", "task_id": "task-x", "resolved": True},
            {"arm": "hive", "task_id": "task-x", "resolved": True},
        ],
    }


def _run(check: dict, text: str, monkeypatch) -> list[str]:
    monkeypatch.setattr(check_claims, "load_artifact",
                        lambda path: _fake_artifact())
    return check_claims.run_check(check, {"doc.md": text})


def test_claim_multi_catches_wrong_and_missing_cells(monkeypatch):
    check = {
        "label": "t", "kind": "multi", "file": "doc.md", "artifact": "x",
        "paths": ["summary.baseline.resolved", "summary.context.resolved",
                  "summary.hive.resolved"],
        "abs_tol": 0.5,
        "regex": r"^\| Resolve \| (\d+)/90 \| (\d+)/90 \| (\d+)/90",
    }
    assert _run(check, "| Resolve | 77/90 | 74/90 | 74/90 |\n",
                monkeypatch) == []
    problems = _run(check, "| Resolve | 99/90 | 74/90 | 74/90 |\n", monkeypatch)
    assert any("MISMATCH" in p and "baseline" in p for p in problems)
    problems = _run(check, "no table here\n", monkeypatch)
    assert any("MISSING" in p for p in problems)


def test_claim_multi_catches_mutated_denominator_and_percent(monkeypatch):
    """A changed /90 denominator or (86%) in a headline cell must fail."""
    check = {
        "label": "t", "kind": "multi", "file": "doc.md", "artifact": "x",
        "paths": ["summary.baseline.resolved", "summary.baseline.tasks",
                  {"path": "summary.baseline.resolve_rate", "percent": True}],
        "abs_tol": 0.5,
        "regex": r"^\| Resolve \| (\d+)/(\d+) \((\d+)%\)",
    }
    # 86% is the correct rounding of 0.8556 — passes.
    assert _run(check, "| Resolve | 77/90 (86%) |\n", monkeypatch) == []
    problems = _run(check, "| Resolve | 77/91 (86%) |\n", monkeypatch)
    assert any("MISMATCH" in p and "tasks" in p for p in problems)
    problems = _run(check, "| Resolve | 77/90 (85%) |\n", monkeypatch)
    assert any("MISMATCH" in p and "resolve_rate" in p for p in problems)


def test_claim_multi_per_spec_tolerances(monkeypatch):
    """A dict spec carries its own tolerance so counts and dollars share a row."""
    check = {
        "label": "t", "kind": "multi", "file": "doc.md", "artifact": "x",
        "paths": ["summary.baseline.resolved",
                  {"path": "summary.baseline.usd_total", "abs_tol": 2e-3}],
        "abs_tol": 0.5,
        "regex": r"^\| Row \| (\d+)/90 \| \$([\d.]+)",
    }
    assert _run(check, "| Row | 77/90 | $0.368 |\n", monkeypatch) == []
    problems = _run(check, "| Row | 77/90 | $0.40 |\n", monkeypatch)
    assert any("MISMATCH" in p and "usd_total" in p for p in problems)


def test_claim_task_row_catches_mutated_cell(monkeypatch):
    check = {
        "label": "t", "kind": "task_row", "file": "doc.md", "artifact": "x",
        "task": "task-x", "arms": ["baseline", "context", "hive"],
        "regex": r"^\| task-x \| (\d+)/(\d+) \| (\d+)/(\d+) \| (\d+)/(\d+) \|",
    }
    assert _run(check, "| task-x | 15/15 | 13/15 | 10/15 |\n",
                monkeypatch) == []
    problems = _run(check, "| task-x | 15/15 | 13/15 | 15/15 |\n", monkeypatch)
    assert any("MISMATCH" in p and "hive" in p for p in problems)


def test_claim_comparison_checks_verdict_word_and_p(monkeypatch):
    check = {
        "label": "t", "kind": "comparison", "file": "doc.md", "artifact": "x",
        "pair": "baseline_vs_hive", "p_group": 1, "verdict_group": 0,
        "verdict_path": "summary.comparisons.baseline_vs_hive.verdict",
        "abs_tol": 1e-4,
        "regex": r"verdict: (\w+) \(p=([\d.]+)\)",
    }
    assert _run(check, "verdict: not_separable (p=1.0)\n", monkeypatch) == []
    problems = _run(check, "verdict: separable (p=1.0)\n", monkeypatch)
    assert any("verdict" in p for p in problems)
    problems = _run(check, "verdict: not_separable (p=0.03)\n", monkeypatch)
    assert any("recomputed" in p for p in problems)


def test_claim_gate_flags_missing_artifact(tmp_path, monkeypatch, capsys):
    """A check whose artifact file is absent must surface as a problem."""
    monkeypatch.setattr(check_claims, "load_artifact",
                        lambda path: (_ for _ in ()).throw(
                            FileNotFoundError(f"{path} is not committed")))
    check = {
        "label": "t", "kind": "single", "file": "doc.md", "artifact": "gone.json",
        "path": "summary.hive.resolved",
        "regex": r"(\d+)",
    }
    # run_check itself raises; main() converts that into a MISSING problem.
    with pytest.raises(FileNotFoundError):
        check_claims.run_check(check, {"doc.md": "74\n"})
