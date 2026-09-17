"""Integrity and provenance tests for scripts/hive_bench.py — no LLM calls."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.hive_bench import (  # noqa: E402
    ScriptedDriver,
    Task,
    TaskIntegrityError,
    ToolExecutor,
    build_provenance,
    run_episode,
)


def _repo(tmp_path: Path, *, passing: bool, test_dir: str = "tests") -> Path:
    """A minimal repo whose suite fails (or passes) on a fresh checkout."""
    repo = tmp_path / "repo"
    (repo / test_dir).mkdir(parents=True)
    body = "assert True" if passing else "assert False, 'real bug'"
    (repo / test_dir / "test_thing.py").write_text(f"def test_thing():\n    {body}\n")
    (repo / "thing.py").write_text("def thing():\n    return 1\n")
    return repo


def _task(repo: Path, test_cmd: str = "python -m pytest tests -q") -> Task:
    return Task(id="fake-task", family="fake", problem_statement="fix it",
                test_cmd=test_cmd, test_timeout_s=60, repo_dir=repo)


# --- write gate -------------------------------------------------------------


def test_write_blocked_for_any_tests_segment(tmp_path):
    ex = ToolExecutor(tmp_path, "python -m pytest tests -q", 60)
    for rel in ("tests/test_x.py", "pkg/tests/test_x.py", "tests/nested/deep.py"):
        out = ex.write_file(rel, "x = 1\n")
        assert out.startswith("ERROR"), rel
        assert not (tmp_path / rel).exists()


def test_write_blocked_for_declared_test_path(tmp_path):
    # The task gates on checks/, not tests/ — writes there must still fail.
    ex = ToolExecutor(tmp_path, "python -m pytest checks -q", 60)
    assert ex.write_file("checks/test_x.py", "x = 1\n").startswith("ERROR")
    assert not (tmp_path / "checks").exists()
    # …while ordinary source paths still write.
    assert ex.write_file("src/thing.py", "x = 1\n").startswith("wrote")


def test_write_allowed_outside_test_paths(tmp_path):
    ex = ToolExecutor(tmp_path, "python -m pytest tests -q", 60)
    assert ex.write_file("thing.py", "x = 1\n") == "wrote thing.py (6 bytes)"
    assert (tmp_path / "thing.py").read_text() == "x = 1\n"


# --- test_cmd is honoured ---------------------------------------------------


def test_run_tests_uses_task_test_cmd(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "checks").mkdir(parents=True)
    (repo / "tests" / "test_pass.py").write_text("def test_ok():\n    assert True\n")
    (repo / "checks" / "test_fail.py").write_text("def test_bad():\n    assert False\n")
    # Hardcoded 'tests' would pass; the declared cmd must run checks/ and fail.
    ex = ToolExecutor(repo, "python -m pytest checks -q", 60)
    passed, out = ex.run_tests()
    assert not passed
    assert "test_fail" in out


# --- fresh-checkout pass is a hard failure -----------------------------------


def test_episode_hard_fails_when_suite_already_passes(tmp_path):
    repo = _repo(tmp_path, passing=True)
    with pytest.raises(TaskIntegrityError, match="fake-task"):
        run_episode(_task(repo), arm="baseline", backend=ScriptedDriver(),
                    stack=None, max_turns=3, max_tokens=100,
                    workdir=tmp_path / "work")


def test_episode_runs_when_suite_fails(tmp_path):
    repo = _repo(tmp_path, passing=False)
    result = run_episode(_task(repo), arm="baseline", backend=ScriptedDriver(),
                         stack=None, max_turns=5, max_tokens=100,
                         workdir=tmp_path / "work")
    assert result.pre_failed
    assert not result.resolved  # scripted driver never fixes anything


# --- provenance --------------------------------------------------------------


class _FakePolicy:
    def predict(self, state):  # pragma: no cover - never called
        raise AssertionError


class _FakeStack:
    def __init__(self, policy):
        self.busybee = policy


def _args(**over):
    base = dict(policy="rule", policy_path=None, temperature=0.0, repeat=1)
    base.update(over)
    return types.SimpleNamespace(**base)


def test_provenance_records_queried_policy_class():
    prov = build_provenance(args=_args(), stack=_FakeStack(_FakePolicy()),
                            policy=None, arms=["baseline", "hive"])
    assert prov["policy"] == "rule"
    assert prov["policy_class"] == "_FakePolicy"
    assert prov["temperature"] == 0.0
    assert prov["repeat"] == 1
    assert "T" in prov["timestamp"]  # ISO-8601
    # git_sha may be a sha or None (non-git checkout) — but the key is there.
    assert "git_sha" in prov and "git_dirty" in prov


def test_provenance_records_trained_policy_path():
    prov = build_provenance(
        args=_args(policy="trained", policy_path="bench/x.joblib",
                   temperature=0.2, repeat=2),
        stack=_FakeStack(_FakePolicy()), policy=None, arms=["hive"])
    assert prov["policy"] == "trained"
    assert prov["policy_path"] == "bench/x.joblib"
    assert prov["temperature"] == 0.2
    assert prov["repeat"] == 2


def test_provenance_null_policy_for_baseline_only():
    prov = build_provenance(args=_args(), stack=None, policy=None,
                            arms=["baseline"])
    assert prov["policy"] is None
    assert prov["policy_class"] is None


# ---------------------------------------------------------------------------
# Dispersion: the pass is the unit a --repeat actually varies
# ---------------------------------------------------------------------------


class _R:
    def __init__(self, arm: str, pass_idx: int, llm_calls: int) -> None:
        self.arm = arm
        self.pass_idx = pass_idx
        self.llm_calls = llm_calls
        self.resolved = False
        self.turns = 1
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.wall_clock_s = 0.1
        self.memory_hit = False
        self.observation_chars = 0
        self.context_chars = 0


def test_per_pass_means_groups_by_pass_and_arm():
    from scripts.hive_bench import per_pass_means

    results = [
        _R("hive", 0, 2), _R("hive", 0, 4),
        _R("hive", 1, 0), _R("hive", 1, 1),
        _R("baseline", 0, 6), _R("baseline", 0, 6),
    ]

    assert per_pass_means(results, "hive") == {0: 3.0, 1: 0.5}
    assert per_pass_means(results, "baseline") == {0: 6.0}
    assert per_pass_means(results, "nobody") == {}


def test_dispersion_refuses_to_imply_a_spread_from_one_pass():
    from scripts.hive_bench import dispersion

    one = dispersion([1.8])
    assert one["passes"] == 1 and one["stderr"] is None
    assert dispersion([])["passes"] == 0

    two = dispersion([2.0, 1.0])
    assert two["passes"] == 2 and two["mean"] == 1.5
    assert two["stderr"] == pytest.approx(0.5)  # sample stderr over 2 passes
    assert (two["min"], two["max"]) == (1.0, 2.0)


def test_summarize_carries_per_pass_means_and_dispersion():
    from scripts.hive_bench import summarize

    results = [_R("hive", 0, 2), _R("hive", 0, 2), _R("hive", 1, 0), _R("hive", 1, 0)]
    summary = summarize(results, "hive")

    assert summary["per_pass_mean_llm_calls"] == {"0": 2.0, "1": 0.0}
    assert summary["llm_calls_dispersion"]["passes"] == 2
    assert summary["mean_llm_calls"] == 1.0  # pooled, for the headline row
