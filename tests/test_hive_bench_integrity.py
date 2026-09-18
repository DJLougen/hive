"""Integrity and provenance tests for scripts/hive_bench.py — no LLM calls."""

from __future__ import annotations

import shutil
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.hive_bench import (  # noqa: E402
    ScriptedDriver,
    StepLog,
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
    for rel in ("tests/test_x.py", "pkg/tests/test_x.py", "tests/nested/deep.py",
                "conftest.py", "pyproject.toml", "sitecustomize.py",
                "pkg/conftest.py"):  # pytest-control files steer the oracle
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


class _WriteThenFinishDriver:
    """Emits write_file -> run_tests -> finish, then keeps finishing."""

    _SCRIPT = ("write_file", "run_tests", "finish")

    def __init__(self) -> None:
        self._i = 0
        self.seen_notes: list[str] = []

    def chat(self, messages, *, max_tokens=0, temperature=0.0, tools=None,
             tool_choice=None):
        for m in messages:
            if isinstance(m.get("content"), str) and "re-check the issue" in m["content"]:
                self.seen_notes.append(m["content"])
        tool = self._SCRIPT[min(self._i, len(self._SCRIPT) - 1)]
        self._i += 1
        from hive.llm import ModelResponse
        if tool == "write_file":
            text = 'ACTION: write_file\nPATH: svc/core.py\nCONTENT:\ndef budget(n):\n    return n // 3\n'
        else:
            text = f"ACTION: {tool}"
        return ModelResponse(text=text, prompt_tokens=0, completion_tokens=0,
                             duration_s=0.0, model="scripted", finish_reason="stop")


def test_finish_after_green_triggers_one_spec_review(tmp_path):
    """Deconfound: every arm gets one spec-review turn before finishing."""
    task = _held_out_task(tmp_path)
    driver = _WriteThenFinishDriver()
    run_episode(task, arm="baseline", backend=driver, stack=None,
                max_turns=8, max_tokens=100, workdir=tmp_path / "work")
    # The spec-review note must reach the model exactly once before finish.
    assert len(driver.seen_notes) == 1
    assert "budget is not shared" in driver.seen_notes[0]


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
    def __init__(self, arm: str, pass_idx: int, llm_calls: int, task_id: str = "t",
                 resolved: bool = False) -> None:
        self.arm = arm
        self.pass_idx = pass_idx
        self.llm_calls = llm_calls
        self.resolved = resolved
        self.turns = 1
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.wall_clock_s = 0.1
        self.memory_hit = False
        self.observation_chars = 0
        self.context_chars = 0
        self.task_id = task_id
        self.held_out = False
        self.crashed = False


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


# ---------------------------------------------------------------------------
# Held-out grading: the artefact graded is the patch, not the workdir
# ---------------------------------------------------------------------------


def _held_out_task(tmp_path: Path) -> Task:
    """A task whose visible smoke suite passes before the fix, and whose
    hidden suite fails until the source is actually fixed."""
    repo = tmp_path / "repo"
    (repo / "smoke").mkdir(parents=True)
    (repo / "svc").mkdir(parents=True)
    (repo / "svc" / "__init__.py").write_text("")
    (repo / "svc" / "core.py").write_text("def budget(n):\n    return n\n")
    # Smoke only exercises the happy path, so it is green on a fresh checkout.
    (repo / "smoke" / "test_smoke.py").write_text(
        "from svc.core import budget\n\n\ndef test_smoke():\n    assert budget(3) == 3\n"
    )
    oracle = tmp_path / "oracle"
    (oracle / "tests").mkdir(parents=True)
    (oracle / "tests" / "test_budget.py").write_text(
        "from svc.core import budget\n\n\ndef test_shared_cap():\n    assert budget(3) == 1\n"
    )
    return Task(id="holdout", family="resilience", problem_statement="budget is not shared",
                test_cmd="python -m pytest smoke -q", test_timeout_s=60, repo_dir=repo,
                oracle_dir=oracle, oracle_cmd="python -m pytest tests -q")


def _write_step(path: str, content: str) -> StepLog:
    return StepLog(turn=0, decision_source="llm", tool="write_file",
                   args={"path": path, "content": content}, observation_bytes=1,
                   context_bytes=1, ok=True, write={"path": path, "content": content})


FIX = "def budget(n):\n    return n // 3\n"


def test_grade_patch_is_broken_before_and_resolved_after(tmp_path):
    from scripts.hive_bench import grade_patch

    task = _held_out_task(tmp_path)
    workdir = tmp_path / "work"
    assert not grade_patch(task, [], workdir=workdir)[0]  # pristine must fail
    resolved, out, grade_dir = grade_patch(task, [_write_step("svc/core.py", FIX)],
                                           workdir=workdir)
    assert resolved, out
    assert (grade_dir / "tests" / "test_budget.py").is_file()


def test_holdout_tests_never_land_in_the_agent_workdir(tmp_path):
    from scripts.hive_bench import grade_patch

    task = _held_out_task(tmp_path)
    workdir = tmp_path / "work"
    _, _, grade_dir = grade_patch(task, [_write_step("svc/core.py", FIX)], workdir=workdir)
    assert not (workdir / "tests").exists()
    assert (grade_dir / "tests").is_dir()
    # …and the pristine repo the agent is shown carries no hidden tests either.
    assert not (task.repo_dir / "tests").exists()


def test_grade_patch_ignores_failed_writes_and_refuses_test_paths(tmp_path):
    from scripts.hive_bench import grade_patch

    task = _held_out_task(tmp_path)
    failed = StepLog(turn=0, decision_source="llm", tool="write_file",
                     args={"path": "svc/core.py", "content": FIX}, observation_bytes=1,
                     context_bytes=1, ok=False,
                     write={"path": "svc/core.py", "content": FIX})
    # A write the executor rejected must not enter the graded patch.
    assert not grade_patch(task, [failed], workdir=tmp_path / "work")[0]
    # And a patch that touches the hidden test path is refused, not applied.
    cheat = _write_step("tests/test_budget.py", "def test_shared_cap():\n    pass\n")
    resolved, out, _ = grade_patch(task, [cheat],
                                   workdir=tmp_path / "work")
    assert not resolved and "read-only test path" in out
    # A pytest-control file (conftest.py) must also be refused: it could
    # deselect the oracle tests without fixing any source.
    harness = _write_step("conftest.py",
                          "def pytest_collection_modifyitems(items):\n    del items[:]\n")
    resolved, out, _ = grade_patch(task, [harness], workdir=tmp_path / "work")
    assert not resolved and "read-only" in out


def test_episode_grades_the_patch_not_the_workdir(tmp_path):
    """End-to-end: a held-out episode is graded in a separate rebuild."""
    from scripts.hive_bench import grade_patch

    task = _held_out_task(tmp_path)
    workdir = tmp_path / "work"
    # run_episode's pre-check uses the *hidden* suite: smoke passing is not a
    # reason to hard-fail the task.
    shutil.copytree(task.repo_dir, workdir)
    gate = ToolExecutor(workdir, task.test_cmd, task.test_timeout_s)
    assert gate.run_tests()[0]  # smoke is green on a fresh checkout
    assert not grade_patch(task, [], workdir=workdir)[0]  # hidden suite is red


# ---------------------------------------------------------------------------
# Arms: what each one turns on
# ---------------------------------------------------------------------------


def test_arm_specs_expand_and_select_the_stack():
    from scripts.hive_bench import ARM_SPECS, arm_uses_hive, arms_for

    assert arms_for("both") == ["baseline", "hive"]
    assert arms_for("all") == ["baseline", "context", "hive"]
    assert arms_for("context") == ["context"]
    assert arm_uses_hive("baseline") is False
    assert arm_uses_hive("context") is True and arm_uses_hive("hive") is True
    assert ARM_SPECS["context"]["compression"] and ARM_SPECS["context"]["memory"]
    assert ARM_SPECS["context"]["routing"] == "escalate-only"
    assert ARM_SPECS["baseline"]["compression"] is False


def test_escalate_only_policy_never_routes():
    from hive.harness import EscalateOnlyPolicy, policy_label

    p = EscalateOnlyPolicy()
    for state in ({}, {"listed": True, "tests_run": 1, "suggested_read": "x.py"}):
        d = p.predict(state)
        assert d["escalated"] is True and d["tool"] == "escalate"
    assert p.stats["routed"] == 0
    assert policy_label(p) == "escalate-only"


# ---------------------------------------------------------------------------
# Statistics: pass^k, exact McNemar, and a verdict that can say "null"
# ---------------------------------------------------------------------------


def test_pass_hat_k_needs_every_repeat_to_pass():
    from scripts.hive_bench import pass_hat_k

    # task a: 3/4 resolved -> pass^4 = 0; task b: 4/4 -> 1
    results = (
        [_R("hive", p, 0, task_id="a", resolved=p < 3) for p in range(4)]
        + [_R("hive", p, 0, task_id="b", resolved=True) for p in range(4)]
    )
    assert pass_hat_k(results, "hive", 1) == {"value": 0.875, "tasks_used": 2}
    assert pass_hat_k(results, "hive", 4) == {"value": 0.5, "tasks_used": 2}
    # A task with fewer repeats than k is skipped, not scored as a failure.
    assert pass_hat_k(results[:2], "hive", 4) == {"value": None, "tasks_used": 0}


def test_mcnemar_exact_counts_discordant_pairs():
    from scripts.hive_bench import mcnemar_exact

    same = mcnemar_exact([True, False], [True, False])
    assert same["discordant"] == 0 and same["p"] == 1.0 and same["both"] == 1

    # 8 tasks where only the hive arm solved it: p = 2 * 2^-8 = 0.0078125
    a = [False] * 8 + [True] * 4
    b = [True] * 8 + [True] * 4
    m = mcnemar_exact(a, b)
    assert (m["a_only"], m["b_only"], m["both"]) == (0, 8, 4)
    assert m["p"] == pytest.approx(0.007812)
    with pytest.raises(ValueError):
        mcnemar_exact([True], [True, False])


def test_verdict_names_a_null_instead_of_dressing_it_up():
    from scripts.hive_bench import verdict

    ceiling = {"resolve_rate": 1.0, "resolve_ci95": [0.7, 1.0]}
    assert verdict(ceiling, ceiling, {"p": 1.0}) == "at_ceiling"

    lo = {"resolve_rate": 0.5, "resolve_ci95": [0.2, 0.8]}
    hi = {"resolve_rate": 0.6, "resolve_ci95": [0.3, 0.9]}
    assert verdict(lo, hi, {"p": 0.3}) == "not_separable"  # inside this n
    # Separated needs BOTH a significant paired test and disjoint Wilson CIs.
    win = {"resolve_rate": 0.9, "resolve_ci95": [0.85, 1.0]}
    assert verdict({"resolve_rate": 0.1, "resolve_ci95": [0.05, 0.4]}, win,
                   {"p": 0.01}) == "separated"
    # A significant paired test with overlapping CIs is still not separation.
    assert verdict(lo, hi, {"p": 0.01}) == "not_separable"


def test_summarize_reports_usd_and_the_grid():
    from scripts.hive_bench import summarize

    results = [_R("hive", 0, 1, task_id="a", resolved=True),
               _R("hive", 1, 1, task_id="a", resolved=True)]
    for r in results:
        r.prompt_tokens, r.completion_tokens = 1_000_000, 0
    s = summarize(results, "hive", price_in=2.0, price_out=0.0)

    assert s["usd_total"] == pytest.approx(4.0)
    assert s["usd_per_resolved_task"] == pytest.approx(2.0)
    assert s["per_task_resolved"] == {"a": "2/2"}
    assert s["pass_hat_k"] == {"1": {"value": 1.0, "tasks_used": 1},
                               "2": {"value": 1.0, "tasks_used": 1}}
    assert s["resolve_ci95"][0] < 1.0  # Wilson, not a bare point estimate
    # cost/0 is undefined, not free — the field is null, never a fake 0.0.
    assert summarize([_R("hive", 0, 1, task_id="a", resolved=False)], "hive")[
        "usd_per_resolved_task"] is None

def test_summarize_nulls_usage_fields_for_unmeasured_rows():
    """A partly-resumed arm publishes null usage, never a zero that reads free."""
    from scripts.hive_bench import summarize

    measured = _R("hive", 0, 1, task_id="a", resolved=True)
    measured.prompt_tokens, measured.completion_tokens = 1000, 500
    resumed = _R("hive", 0, 1, task_id="b", resolved=True)
    resumed.usage_recorded = False
    s = summarize([measured, resumed], "hive")

    assert s["resolved"] == 2                       # outcomes still count
    assert s["usd_total"] is None                   # usage unknown, not $0
    assert s["usd_per_resolved_task"] is None
    assert s["prompt_tokens"] is None
    assert s["mean_llm_calls"] is None
    assert s["per_pass_mean_llm_calls"] is None
    assert s["llm_calls_dispersion"] is None
    assert s["episodes_without_usage"] == 1
    # resolve-side fields stay real
    assert s["resolve_rate"] == 1.0
    assert s["per_task_resolved"] == {"a": "1/1", "b": "1/1"}


# ---------------------------------------------------------------------------
# --verify-tasks: the oracle pre-flight every held-out task must pass
# ---------------------------------------------------------------------------


def _oracle_task(tmp_path: Path, *, smoke_red: bool = True,
                 patch: str = "oracle/solution.patch",
                 write_patch: bool = True,
                 patch_fixes_smoke: bool = True) -> Task:
    """Author a miniature held-out task on disk, then point a Task at it.

    The smoke suite covers the disclosed spec, so it must be RED on the
    pristine repo (the reproduction signal) and GREEN once the reference
    patch lands. ``smoke_red=False`` authors a suite that does not cover the
    spec — the gate must reject it. ``patch_fixes_smoke=False`` authors a
    patch that fixes the oracle but leaves smoke red — also a reject.
    """
    task_dir = tmp_path / "tasks" / "mini"
    repo = task_dir / "repo"
    (repo / "smoke").mkdir(parents=True)
    (repo / "svc").mkdir(parents=True)
    (repo / "svc" / "__init__.py").write_text("")
    (repo / "svc" / "core.py").write_text("def budget(n):\n    return n\n")
    # Two smoke tests: a happy-path check that passes either way, and a
    # disclosed-spec check that is red on the buggy repo (budget(3) should be
    # 1 after the fix, is 3 before) — like the real tasks.
    (repo / "smoke" / "test_smoke.py").write_text(
        "from svc.core import budget\n\n\n"
        "def test_happy_path():\n    assert budget(0) == 0\n\n\n"
        "def test_spec_requirement():\n    assert budget(3) == "
        + ("1" if smoke_red else "3") + "\n")
    (task_dir / "oracle" / "tests").mkdir(parents=True)
    (task_dir / "oracle" / "tests" / "test_hidden.py").write_text(
        "from svc.core import budget\n\n\ndef test_hidden():\n    assert budget(3) == 1\n")
    if write_patch:
        (task_dir / "oracle" / "solution.patch").write_text(
            "--- a/svc/core.py\n"
            "+++ b/svc/core.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def budget(n):\n"
            "-    return n\n"
            "+    return " + ("n // 3" if patch_fixes_smoke else "n + 1") + "\n"
        )
    return Task(id="mini", family="mini", problem_statement="fix it",
                test_cmd="python -m pytest smoke -q", test_timeout_s=60, repo_dir=repo,
                oracle_dir=task_dir / "oracle", oracle_cmd="python -m pytest tests -q",
                solution_patch=task_dir / patch)


def test_verify_tasks_passes_a_well_formed_task(tmp_path, capsys):
    from scripts.hive_bench import verify_tasks

    assert verify_tasks([_oracle_task(tmp_path)]) == 0
    assert "smoke=red-ok smoke-fixed=ok broken=ok oracle=ok hidden=ok" in capsys.readouterr().out


def test_verify_tasks_fails_when_the_repo_leaks_its_own_tests(tmp_path, capsys):
    from scripts.hive_bench import verify_tasks

    task = _oracle_task(tmp_path)
    (task.repo_dir / "tests").mkdir()
    assert verify_tasks([task]) == 1
    assert "hidden=FAIL" in capsys.readouterr().out

def test_verify_tasks_fails_when_the_smoke_suite_does_not_cover_the_spec(tmp_path):
    from scripts.hive_bench import verify_tasks

    # Smoke green on the buggy repo means the disclosed spec is unchecked —
    # a green run_tests would certify an incomplete fix.
    assert verify_tasks([_oracle_task(tmp_path, smoke_red=False)]) == 1


def test_verify_tasks_fails_when_the_fix_leaves_smoke_red(tmp_path):
    from scripts.hive_bench import verify_tasks

    # The reference patch fixes the oracle but not the visible suite — the
    # task would be unresolvable from the agent's seat.
    assert verify_tasks([_oracle_task(tmp_path, patch_fixes_smoke=False)]) == 1


def test_verify_tasks_fails_when_the_fix_does_not_apply(tmp_path):
    from scripts.hive_bench import verify_tasks

    assert verify_tasks([_oracle_task(tmp_path, patch="oracle/test_hidden.py",
                                      write_patch=False)]) == 1


def test_verify_tasks_refuses_to_pass_with_nothing_checked(tmp_path, capsys):
    from scripts.hive_bench import verify_tasks

    legacy = _task(_repo(tmp_path, passing=False))  # no oracle_dir
    assert verify_tasks([legacy]) == 1
    assert "nothing was checked" in capsys.readouterr().out
