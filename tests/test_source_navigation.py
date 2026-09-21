"""Tests for hive/source_navigation.py and the --source-navigation wiring.

No LLM calls: the integration test drives run_episode with a fake backend
and a real RuleBasedRoutingPolicy, so the only difference between the two
navigation modes is who decides the post-facade read.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hive.source_navigation import suggest_reexport  # noqa: E402
from scripts.hive_bench import (  # noqa: E402
    Task,
    build_provenance,
    run_episode,
)


def _pkg(tmp_path: Path, init: str, files: dict[str, str] | None = None) -> Path:
    """A fresh generic package: pkg/__init__.py with ``init`` plus extras."""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(init)
    for rel, body in (files or {}).items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return tmp_path


def _suggest(tmp_path: Path, init: str, files: dict[str, str] | None = None,
             *, source: str = "pkg/__init__.py",
             files_read: list[str] | None = None) -> str | None:
    workdir = _pkg(tmp_path, init, files)
    return suggest_reexport(init, source_path=source, workdir=workdir,
                            files_read=files_read or ["pkg/__init__.py"])


# --- pure facades suggest their single target --------------------------------


def test_basic_relative_reexport(tmp_path):
    assert _suggest(tmp_path, "from .impl import thing\n",
                    {"pkg/impl.py": "def thing():\n    return 1\n"}) == "pkg/impl.py"


def test_duplicate_imports_same_target_converge(tmp_path):
    init = '"""pkg facade."""\nfrom .impl import a\nfrom .impl import b\n__all__ = ["a", "b"]\n'
    assert _suggest(tmp_path, init, {"pkg/impl.py": "a = b = 1\n"}) == "pkg/impl.py"


def test_nested_parent_relative_import(tmp_path):
    # pkg/sub/__init__.py reaching up one level to pkg/impl.py.
    workdir = _pkg(tmp_path, "", {"pkg/sub/__init__.py": "from ..impl import thing\n",
                                  "pkg/impl.py": "thing = 1\n"})
    assert suggest_reexport("from ..impl import thing\n",
                            source_path="pkg/sub/__init__.py", workdir=workdir,
                            files_read=["pkg/sub/__init__.py"]) == "pkg/impl.py"


def test_absolute_local_import(tmp_path):
    assert _suggest(tmp_path, "from pkg.impl import thing\n",
                    {"pkg/impl.py": "thing = 1\n"}) == "pkg/impl.py"


def test_from_dot_import_submodule(tmp_path):
    assert _suggest(tmp_path, "from . import impl\n",
                    {"pkg/impl.py": "thing = 1\n"}) == "pkg/impl.py"


def test_package_target_resolves_to_its_init(tmp_path):
    assert _suggest(tmp_path, "from .sub import thing\n",
                    {"pkg/sub/__init__.py": "thing = 1\n"}) == "pkg/sub/__init__.py"


# --- refusals -----------------------------------------------------------------


def test_target_already_read_is_not_suggested(tmp_path):
    assert _suggest(tmp_path, "from .impl import thing\n",
                    {"pkg/impl.py": "thing = 1\n"},
                    files_read=["pkg/__init__.py", "pkg/impl.py"]) is None


def test_multiple_distinct_targets_refuse(tmp_path):
    init = "from .a import x\nfrom .b import y\n"
    assert _suggest(tmp_path, init, {"pkg/a.py": "x = 1\n",
                                     "pkg/b.py": "y = 1\n"}) is None


def test_star_import_refuses(tmp_path):
    assert _suggest(tmp_path, "from .impl import *\n",
                    {"pkg/impl.py": "thing = 1\n"}) is None


def test_external_import_refuses(tmp_path):
    assert _suggest(tmp_path, "from os import getcwd\n") is None


def test_ordinary_import_refuses(tmp_path):
    assert _suggest(tmp_path, "import pkg.impl\n",
                    {"pkg/impl.py": "thing = 1\n"}) is None


def test_dynamic_import_refuses(tmp_path):
    init = 'import importlib\nimpl = importlib.import_module(".impl", __name__)\n'
    assert _suggest(tmp_path, init, {"pkg/impl.py": "thing = 1\n"}) is None


def test_runtime_code_refuses(tmp_path):
    init = "import sys\nfrom .impl import thing\n"
    assert _suggest(tmp_path, init, {"pkg/impl.py": "thing = 1\n"}) is None
    init2 = "try:\n    from .impl import thing\nexcept ImportError:\n    thing = None\n"
    assert _suggest(tmp_path, init2, {"pkg/impl.py": "thing = 1\n"}) is None


def test_syntax_error_refuses(tmp_path):
    assert _suggest(tmp_path, "from .impl import (\n",
                    {"pkg/impl.py": "thing = 1\n"}) is None


def test_source_path_escape_refuses(tmp_path):
    workdir = _pkg(tmp_path, "from .impl import thing\n",
                   {"pkg/impl.py": "thing = 1\n"})
    assert suggest_reexport("from .impl import thing\n",
                            source_path="../outside/__init__.py",
                            workdir=workdir, files_read=[]) is None


def test_relative_import_climbing_out_refuses(tmp_path):
    # A top-level package's __init__ cannot reach above the checkout.
    assert _suggest(tmp_path, "from ..impl import thing\n",
                    {"impl.py": "thing = 1\n"}) is None


def test_symlink_escape_refuses(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "impl.py").write_text("thing = 1\n")
    workdir = tmp_path / "repo"
    pkg = workdir / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("from .impl import thing\n")
    (pkg / "impl.py").symlink_to(outside / "impl.py")
    assert suggest_reexport("from .impl import thing\n",
                            source_path="pkg/__init__.py", workdir=workdir,
                            files_read=["pkg/__init__.py"]) is None


def test_module_package_ambiguity_refuses(tmp_path):
    # Both impl.py and impl/__init__.py exist — the import is ambiguous.
    assert _suggest(tmp_path, "from .impl import thing\n",
                    {"pkg/impl.py": "thing = 1\n",
                     "pkg/impl/__init__.py": "thing = 2\n"}) is None


def test_unresolvable_mixed_with_resolvable_refuses(tmp_path):
    init = "from .impl import thing\nfrom .missing import other\n"
    assert _suggest(tmp_path, init, {"pkg/impl.py": "thing = 1\n"}) is None


def test_oversized_source_refuses(tmp_path):
    init = "from .impl import thing\n" + "# pad\n" * 20000
    assert _suggest(tmp_path, init, {"pkg/impl.py": "thing = 1\n"}) is None


def test_non_init_source_refuses(tmp_path):
    workdir = _pkg(tmp_path, "from .impl import thing\n",
                   {"pkg/impl.py": "thing = 1\n"})
    assert suggest_reexport("from .impl import thing\n",
                            source_path="pkg/impl.py", workdir=workdir,
                            files_read=["pkg/impl.py"]) is None


# --- run_episode wiring --------------------------------------------------------


def _facade_repo(tmp_path: Path) -> Path:
    """A repo whose package __init__ is a pure facade over a buggy impl."""
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text(
        '"""Public API."""\nfrom .impl import double\n__all__ = ["double"]\n')
    (repo / "pkg" / "impl.py").write_text("def double(n):\n    return n + 1  # bug\n")
    (repo / "tests" / "test_pkg.py").write_text(
        "from pkg import double\n\n"
        "def test_double():\n    assert double(2) == 4\n")
    return repo


class _FacadeFixBackend:
    """Fake backend: reads the facade, then the impl, writes the fix,
    finishes after the spec-review note. Every reply is an LLM call the
    test can count."""

    def __init__(self) -> None:
        self.calls = 0
        self.facade_observations: list[str] = []

    def chat(self, messages, *, max_tokens=0, temperature=0.0, tools=None,
             tool_choice=None):
        self.calls += 1
        transcript = "\n".join(str(m.get("content", "")) for m in messages)
        for m in messages:
            content = str(m.get("content", ""))
            if "OBSERVATION[read_file]" in content and "Public API" in content:
                self.facade_observations.append(content)
        if "re-check the issue" in transcript:
            text = "ACTION: finish"
        elif "def double(n):" in transcript:
            text = ("ACTION: write_file\nPATH: pkg/impl.py\n"
                    "```\ndef double(n):\n    return n * 2\n```")
        elif "Public API" in transcript:
            text = "ACTION: read_file\nPATH: pkg/impl.py"
        else:
            text = "ACTION: read_file\nPATH: pkg/__init__.py"
        from hive.llm import ModelResponse

        return ModelResponse(text=text, prompt_tokens=0, completion_tokens=0,
                             duration_s=0.0, model="fake", finish_reason="stop")


class _PolicyStack:
    """Minimal stack: real RuleBasedRoutingPolicy for routing, pass-through
    compression, no memory. Same interface run_episode consumes."""

    def __init__(self) -> None:
        from hive.harness import RuleBasedRoutingPolicy

        self.busybee = RuleBasedRoutingPolicy()

    def route(self, state):
        action = self.busybee.predict(dict(state))
        return types.SimpleNamespace(
            tool=action.get("tool"), args=action.get("args") or {},
            escalated=bool(action.get("escalated")))

    def compress(self, role, content):
        return types.SimpleNamespace(content=content, label=None)

    def recall(self, key, default=None):
        return default

    def remember(self, *a, **kw):
        return None


def _run_facade_episode(tmp_path: Path, mode: str):
    repo = _facade_repo(tmp_path)
    task = Task(id="facade", family="facade", problem_statement="fix double()",
                test_cmd="python -m pytest tests -q", test_timeout_s=60,
                repo_dir=repo)
    backend = _FacadeFixBackend()
    result = run_episode(task, arm="hive", backend=backend,
                         stack=_PolicyStack(), max_turns=12, max_tokens=100,
                         workdir=tmp_path / f"work-{mode}",
                         source_navigation=mode)
    return result, backend


def test_reexports_routes_impl_read_without_llm(tmp_path):
    """The facade's re-export target is read on a policy decision, not an
    LLM call — while the patch, the facade observation, and the verdict are
    identical to legacy."""
    legacy, legacy_be = _run_facade_episode(tmp_path / "a", "legacy")
    reexp, reexp_be = _run_facade_episode(tmp_path / "b", "reexports")

    def impl_read(result):
        return next(s for s in result.steps
                    if s.tool == "read_file" and s.args.get("path") == "pkg/impl.py")

    # The candidate replaces an implementation-read LLM decision.
    assert impl_read(legacy).decision_source == "llm"
    assert impl_read(reexp).decision_source == "policy"
    assert reexp.llm_calls == legacy.llm_calls - 1

    # Same tool sequence, same patch, same verdict, same facade content.
    assert [s.tool for s in reexp.steps] == [s.tool for s in legacy.steps]
    def patch(r):
        return {s.write["path"]: s.write["content"] for s in r.steps if s.write}
    assert patch(reexp) == patch(legacy) == {
        "pkg/impl.py": "def double(n):\n    return n * 2\n"}
    assert reexp.resolved and legacy.resolved
    assert reexp_be.facade_observations
    assert set(reexp_be.facade_observations) == set(legacy_be.facade_observations)

def test_legacy_default_unchanged(tmp_path):
    """Default kwarg is legacy: no re-export suggestion, impl read is an
    LLM decision."""
    repo = _facade_repo(tmp_path)
    task = Task(id="facade", family="facade", problem_statement="fix double()",
                test_cmd="python -m pytest tests -q", test_timeout_s=60,
                repo_dir=repo)
    result = run_episode(task, arm="hive", backend=_FacadeFixBackend(),
                         stack=_PolicyStack(), max_turns=12, max_tokens=100,
                         workdir=tmp_path / "work")
    impl_read = next(s for s in result.steps
                     if s.tool == "read_file" and s.args.get("path") == "pkg/impl.py")
    assert impl_read.decision_source == "llm"


def test_invalid_source_navigation_rejected(tmp_path):
    repo = _facade_repo(tmp_path)
    task = Task(id="facade", family="facade", problem_statement="fix double()",
                test_cmd="python -m pytest tests -q", test_timeout_s=60,
                repo_dir=repo)
    with pytest.raises(ValueError, match="source_navigation"):
        run_episode(task, arm="baseline", backend=_FacadeFixBackend(),
                    stack=None, max_turns=3, max_tokens=100,
                    workdir=tmp_path / "work", source_navigation="bogus")


def test_provenance_records_source_navigation():
    args = types.SimpleNamespace(policy="rule", policy_path=None,
                                 temperature=0.0, repeat=1,
                                 source_navigation="reexports")
    prov = build_provenance(args=args, stack=None, policy=None,
                            arms=["baseline"])
    assert prov["source_navigation"] == "reexports"
    # A namespace predating the flag records the honest default.
    legacy_args = types.SimpleNamespace(policy="rule", policy_path=None,
                                        temperature=0.0, repeat=1)
    prov = build_provenance(args=legacy_args, stack=None, policy=None,
                            arms=["baseline"])
    assert prov["source_navigation"] == "legacy"


# --- trained classifier: suggestion flows through classify/resolve ------------


class _StubClf:
    """Classifier stub returning a fixed probability vector."""

    def __init__(self, probs):
        self._probs = probs

    def predict_proba(self, xs):
        import numpy as np

        return np.array([self._probs])


def test_trained_classifier_routes_suggested_read_without_bypass():
    """With the reexport suggestion in state, a fitted CPURouterPolicy
    routes read_file only when its own confidence floor passes — the
    suggestion never bypasses classify/resolve."""
    from hive.cpu_policy import CPURouterPolicy

    state = {"listed": True, "tests_run": 1, "tests_passed": False,
             "writes": 0, "files_read": ["tests/test_pkg.py", "pkg/__init__.py"],
             "suggested_read": "pkg/impl.py", "step": 4,
             "last_tool": "read_file"}

    high = CPURouterPolicy(threshold=0.55)
    high.clf = _StubClf([0.1, 0.9])          # read_file wins
    high.classes_ = ["list_files", "read_file"]
    decision = high.predict(dict(state))
    assert decision["tool"] == "read_file"
    assert decision["args"] == {"path": "pkg/impl.py"}
    assert not decision["escalated"]

    low = CPURouterPolicy(threshold=0.55)
    low.clf = _StubClf([0.5, 0.4])           # below the floor
    low.classes_ = ["list_files", "read_file"]
    decision = low.predict(dict(state))
    assert decision["escalated"]
    assert decision["args"]["reason"] == "below confidence floor"
