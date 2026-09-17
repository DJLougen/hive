"""hive-bench — real tool-execution benchmark for Hive.

Replaces the old simulated SWE-bench eval. Everything here is measured, not
simulated:

* tasks are real repos on disk with real failing pytest suites
  (``benchmarks/tasks/<id>/repo``),
* the agent acts through real tools (list_files / read_file / grep /
  run_tests / write_file / finish) executed by this harness,
* the resolve check is a real ``pytest`` run at the end of each episode,
* LLM calls are real calls to an OpenAI-compatible endpoint with token
  counts taken from the API ``usage`` field,
* the baseline arm sends every decision to the LLM; the hive arm routes
  mechanical transitions through ``hive.harness.load_routing_policy()``,
  compresses tool observations through ``stack.compress()``, and recalls /
  records fixes through ``stack.brain``.

Usage:
    python scripts/hive_bench.py --backend openai \
        --endpoint https://api.groq.com/openai \
        --api-key-env GROQ_API_KEY --model openai/gpt-oss-120b

    # plumbing smoke (no LLM): python scripts/hive_bench.py --driver scripted
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_log = logging.getLogger("hive.bench")

try:                                  # imported as a module (tests, other scripts)
    from scripts.trace_bench import wilson
except ModuleNotFoundError:           # run directly: sys.path[0] is scripts/
    from trace_bench import wilson

TOOLS = ("list_files", "read_file", "grep", "run_tests", "write_file", "finish")
MAX_OBSERVATION_CHARS = 24_000

# What each arm turns on. ``baseline`` is the plain LLM loop; ``context`` keeps
# Hive's compression + causal memory but routes nothing (every decision goes to
# the model), which isolates "smaller, replayable context" from "the policy
# decides for us"; ``hive`` is the full stack. ``both`` = baseline + hive, the
# historical pair, kept so earlier artifacts stay reproducible.
ARM_SPECS: dict[str, dict[str, Any]] = {
    "baseline": {"routing": "none", "compression": False, "memory": False, "stack": None},
    "context": {"routing": "escalate-only", "compression": True, "memory": True,
                "stack": "EscalateOnlyPolicy"},
    "hive": {"routing": "policy", "compression": True, "memory": True, "stack": "HiveStack"},
}
ARM_COMBOS: dict[str, list[str]] = {
    "both": ["baseline", "hive"],
    "all": ["baseline", "context", "hive"],
}


def arms_for(arm: str) -> list[str]:
    """Expand an ``--arm`` value into the arms to run."""
    return list(ARM_COMBOS.get(arm, [arm]))


def arm_uses_hive(arm: str) -> bool:
    """Does this arm run through a HiveStack at all?"""
    return ARM_SPECS.get(arm, {}).get("stack") is not None


# Native function-calling schema — the agent emits real tool_calls.
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {"type": "function", "function": {"name": "list_files",
     "description": "List all files in the repo", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "read_file",
     "description": "Read a repo file", "parameters": {"type": "object",
      "properties": {"path": {"type": "string", "description": "repo-relative path"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "grep",
     "description": "Search repo files for a pattern", "parameters": {"type": "object",
      "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}}},
    {"type": "function", "function": {"name": "run_tests",
     "description": "Run the repo's pytest suite", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "write_file",
     "description": "Overwrite a repo file with new contents (tests/ is read-only)",
      "parameters": {"type": "object", "properties": {
          "path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "finish",
     "description": "Signal the task is complete", "parameters": {"type": "object", "properties": {}}}},
]

SYSTEM_PROMPT = """You are a software-engineering agent. Fix the bug described in the issue so the repo's test suite passes.

Each reply must contain EXACTLY ONE action in this format:

ACTION: <tool>
<payload>

Tools:
- list_files          (no payload) — list repo files
- read_file           payload: PATH: <relative path>
- grep                payload: PATTERN: <text>
- run_tests           (no payload) — run the repo's pytest suite
- write_file          payload:
                      PATH: <relative path>
                      CONTENT:
                      ```<full new file contents>```
- finish              (no payload) — you are done

Rules: make exactly one tool call per reply (or one ACTION: block if tool calls are unavailable). The tests/ directory is read-only — fix the source, never the tests.
"""


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Task:
    id: str
    family: str
    problem_statement: str
    test_cmd: str
    test_timeout_s: int
    repo_dir: Path
    # Held-out grading (see ``grade_patch``). When ``oracle_dir`` is set, the
    # agent-facing ``test_cmd`` is a visible smoke suite and ``oracle_cmd`` —
    # run against a pristine rebuild plus the episode's patch and the tests in
    # ``oracle_dir/tests`` — is what decides ``resolved``.
    oracle_dir: Path | None = None
    oracle_cmd: str = "python -m pytest tests -q"
    solution_patch: Path | None = None  # reference fix; --verify-tasks only
    # Commit date the reference fix became public, or None while it is not.
    # A capability claim is only valid for runs made before this date.
    solutions_public_since: str | None = None


_DEFAULT_TEST_CMD = "python -m pytest tests -q"


def load_tasks(suite_dir: Path, only: list[str] | None = None) -> list[Task]:
    suite = json.loads((suite_dir / "suite.json").read_text())
    tasks: list[Task] = []
    for task_id in suite["tasks"]:
        if only and task_id not in only:
            continue
        task_dir = suite_dir / task_id
        meta = json.loads((task_dir / "task.json").read_text())
        oracle_rel = meta.get("oracle_dir")
        patch_rel = meta.get("solution_patch")
        tasks.append(
            Task(
                id=meta["id"],
                family=meta.get("family", "misc"),
                problem_statement=meta["problem_statement"],
                test_cmd=meta.get("test_cmd", _DEFAULT_TEST_CMD),
                test_timeout_s=int(meta.get("test_timeout_s", 60)),
                repo_dir=task_dir / "repo",
                oracle_dir=(task_dir / oracle_rel) if oracle_rel else None,
                oracle_cmd=meta.get("oracle_cmd", _DEFAULT_TEST_CMD),
                solution_patch=(task_dir / patch_rel) if patch_rel else None,
                solutions_public_since=meta.get("solutions_public_since"),
            )
        )
    return tasks


# ---------------------------------------------------------------------------
# Real tools
# ---------------------------------------------------------------------------


class ToolExecutor:
    """Execute agent actions against a real working copy of the repo."""

    # pytest flags that consume the following token (so it isn't a path).
    _VALUE_FLAGS = {"-k", "-m", "--maxfail", "--timeout", "--basetemp",
                    "--rootdir", "-c", "--confcutdir", "--junitxml", "-o"}

    def __init__(self, workdir: Path, test_cmd: str, test_timeout_s: int) -> None:
        self.workdir = workdir.resolve()
        self.test_cmd = test_cmd
        self.test_timeout_s = test_timeout_s
        self.test_paths = self._declared_test_paths(test_cmd)

    @classmethod
    def _declared_test_paths(cls, test_cmd: str) -> tuple[str, ...]:
        """Repo-relative paths the task's ``test_cmd`` actually runs.

        Writes under any of these are blocked, so a task that declares e.g.
        ``pytest checks`` can't have its gate edited via ``checks/``.
        """
        try:
            tokens = shlex.split(test_cmd)
        except ValueError:
            return ("tests",)
        paths: list[str] = []
        skip_next = False
        for tok in tokens:
            if skip_next:
                skip_next = False
                continue
            if tok in cls._VALUE_FLAGS:
                skip_next = True
                continue
            if tok.startswith("-"):
                continue
            if tok in ("python", "python3", "pytest") or tok.endswith("/pytest"):
                continue
            if tok == "-m":
                skip_next = True
                continue
            paths.append(tok.rstrip("/"))
        return tuple(paths) if paths else ("tests",)

    def _resolve(self, rel: str) -> Path:
        p = (self.workdir / rel).resolve()
        if not str(p).startswith(str(self.workdir.resolve()) + os.sep) and p != self.workdir.resolve():
            raise ValueError(f"path escapes repo: {rel!r}")
        return p

    def list_files(self) -> str:
        files = sorted(
            str(p.relative_to(self.workdir))
            for p in self.workdir.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        )
        return "\n".join(files) if files else "(empty repo)"

    def read_file(self, rel: str) -> str:
        try:
            return self._resolve(rel).read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError) as exc:
            return f"ERROR: {exc}"

    def grep(self, pattern: str) -> str:
        try:
            out = subprocess.run(
                ["rg", "-n", "--no-heading", pattern, "."],
                cwd=self.workdir, capture_output=True, text=True, timeout=30,
            )
        except FileNotFoundError:
            hits = []
            for p in self.workdir.rglob("*"):
                if p.is_file() and "__pycache__" not in p.parts:
                    for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                        if pattern in line:
                            hits.append(f"{p.relative_to(self.workdir)}:{i}:{line}")
            return "\n".join(hits) or "(no matches)"
        return (out.stdout or "(no matches)")[:MAX_OBSERVATION_CHARS]

    def run_tests(self) -> tuple[bool, str]:
        argv = shlex.split(self.test_cmd)
        if argv and argv[0] in ("python", "python3"):
            argv[0] = sys.executable  # run under this interpreter, not PATH's
        try:
            out = subprocess.run(
                argv,
                cwd=self.workdir, capture_output=True, text=True,
                timeout=self.test_timeout_s,
            )
        except subprocess.TimeoutExpired:
            return False, f"ERROR: tests timed out after {self.test_timeout_s}s"
        except (OSError, ValueError) as exc:
            return False, f"ERROR: test_cmd {self.test_cmd!r} failed to run: {exc}"
        text = (out.stdout + "\n" + out.stderr).strip()
        return out.returncode == 0, text

    def write_file(self, rel: str, content: str) -> str:
        try:
            p = self._resolve(rel)
        except ValueError as exc:
            return f"ERROR: {exc}"
        rel_parts = p.relative_to(self.workdir).parts
        # Any tests/ directory segment is read-only, wherever it sits.
        if "tests" in rel_parts:
            return "ERROR: tests/ is read-only — fix the source, not the tests"
        # And so is every path the task's test_cmd actually gates on.
        for declared in self.test_paths:
            dparts = Path(declared).parts
            if rel_parts[: len(dparts)] == dparts:
                return (f"ERROR: {declared}/ is the declared test path — "
                        "fix the source, not the tests")
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        except OSError as exc:
            return f"ERROR: {exc}"
        return f"wrote {rel} ({len(content)} bytes)"



# ---------------------------------------------------------------------------
# Action parsing
# ---------------------------------------------------------------------------

_ACTION_RE = re.compile(r"ACTION:\s*([a-z_]+)", re.IGNORECASE)
_PATH_RE = re.compile(r"PATH:\s*(\S+)", re.IGNORECASE)
_PATTERN_RE = re.compile(r"PATTERN:\s*(.+)", re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:[a-z]*)\n(.*?)```", re.DOTALL)


def parse_action(text: str, tool_calls: list[dict[str, Any]] | None = None) -> tuple[str | None, dict[str, str]]:
    """Extract one action from an LLM reply — native tool_calls first, then
    the plain-text ACTION: fallback. Returns (tool, args)."""
    if tool_calls:
        call = tool_calls[0].get("function", {})
        try:
            return call.get("name"), json.loads(call.get("arguments") or "{}")
        except (json.JSONDecodeError, TypeError):
            return call.get("name"), {}
    matches = _ACTION_RE.findall(text)
    if not matches:
        return None, {}
    tool = matches[-1].lower()
    tail = text[text.rfind(matches[-1]) :]
    if tool == "read_file":
        m = _PATH_RE.search(tail)
        return tool, {"path": m.group(1) if m else ""}
    if tool == "grep":
        m = _PATTERN_RE.search(tail)
        return tool, {"pattern": m.group(1).strip() if m else ""}
    if tool == "write_file":
        m = _PATH_RE.search(tail)
        fence = _FENCE_RE.search(tail)
        content = fence.group(1) if fence else ""
        return tool, {"path": m.group(1) if m else "", "content": content}
    if tool in ("list_files", "run_tests", "finish"):
        return tool, {}
    return None, {}


def chat_with_retry(backend: Any, messages: list[dict[str, str]], *, max_tokens: int,
                    temperature: float = 0.0, retries: int = 4) -> Any:
    delay = 5.0
    for attempt in range(retries):
        try:
            return backend.chat(
                messages, max_tokens=max_tokens, temperature=temperature,
                tools=TOOL_SCHEMAS, tool_choice="auto",
            )
        except Exception as exc:
            if attempt == retries - 1:
                raise
            _log.warning("LLM call failed (%s); retrying in %.0fs", exc, delay)
            time.sleep(delay)
            delay *= 2


class ScriptedDriver:
    """Deterministic no-LLM driver for plumbing smoke tests only."""

    _SCRIPT = ("list_files", "run_tests", "finish")

    def __init__(self) -> None:
        self._i = 0

    def chat(self, messages: list[dict[str, str]], *, max_tokens: int = 0,
             temperature: float = 0.0, tools: Any = None,
             tool_choice: Any = None) -> Any:
        tool = self._SCRIPT[min(self._i, len(self._SCRIPT) - 1)]
        self._i += 1
        from hive.llm import ModelResponse

        return ModelResponse(
            text=f"ACTION: {tool}", prompt_tokens=0, completion_tokens=0,
            duration_s=0.0, model="scripted", finish_reason="stop",
        )


# ---------------------------------------------------------------------------
# Agent runner
# ---------------------------------------------------------------------------


@dataclass
class StepLog:
    turn: int
    decision_source: str          # "policy" | "llm" | "driver"
    tool: str
    args: dict[str, str]
    observation_bytes: int
    context_bytes: int            # what the LLM will actually see (post-compression for hive arm)
    compress_label: str | None = None
    ok: bool = True               # the tool call succeeded (no ERROR observation)
    # Full, untruncated write payload (``args`` below is display-truncated at
    # 120 chars, which would corrupt a patch). Present only for successful
    # write_file steps — this is exactly what ``grade_patch`` replays.
    write: dict[str, str] | None = None


@dataclass
class AgentResult:
    task_id: str
    arm: str
    resolved: bool
    pre_failed: bool                # task's tests really failed before the agent ran
    turns: int
    llm_calls: int
    prompt_tokens: int
    completion_tokens: int
    wall_clock_s: float
    memory_hit: bool
    observation_chars: int
    context_chars: int
    pass_idx: int = 0               # which repeat pass (memory replay check)
    held_out: bool = False          # graded against tests the agent never saw
    steps: list[StepLog] = field(default_factory=list)


def _extract_suggested_read(test_output: str, workdir: Path) -> str | None:
    """Real signal: deepest repo .py file named in the pytest output.

    Handles both traceback style (``File "/abs/x.py", line N``) and the
    short-summary style (``/abs/x.py:N: Error`` or ``tests/x.py:N:``).
    """
    candidates = re.findall(r'File "([^"]+\.py)"', test_output)
    candidates += re.findall(r"^([\w./-]+\.py):\d+", test_output, flags=re.MULTILINE)
    for path in reversed(candidates):
        try:
            rel = Path(path).resolve().relative_to(workdir.resolve())
        except ValueError:
            # Relative path from pytest's short summary — resolve against workdir.
            candidate = (workdir / path).resolve()
            try:
                rel = candidate.relative_to(workdir.resolve())
            except ValueError:
                continue
        if "__pycache__" not in rel.parts:
            return str(rel)
    return None


_IMPORT_RE = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.MULTILINE)


def _imports_under_test(test_content: str, workdir: Path) -> str | None:
    """Real signal: the repo module a test file imports — the unit under test."""
    for m in _IMPORT_RE.finditer(test_content):
        mod = (m.group(1) or m.group(2) or "").split(".")[0]
        if not mod or mod in ("tests", "os", "sys", "re", "time", "json", "pytest"):
            continue
        for candidate in (f"{mod}.py", f"{mod}/__init__.py"):
            if (workdir / candidate).is_file():
                return candidate
    return None


def _truncate(text: str) -> str:
    if len(text) <= MAX_OBSERVATION_CHARS:
        return text
    return text[: MAX_OBSERVATION_CHARS // 2] + "\n…[truncated]…\n" + text[-MAX_OBSERVATION_CHARS // 2 :]


class TaskIntegrityError(RuntimeError):
    """The task's suite already passes on a fresh checkout — a resolve would
    be meaningless, so the episode is a hard failure, not a warning."""


_COPY_IGNORE = shutil.ignore_patterns("__pycache__", ".pytest_cache")


def grade_patch(task: Task, steps: list[StepLog], *, workdir: Path) -> tuple[bool, str, Path]:
    """Apply the episode's writes to a fresh copy of ``task.repo_dir``, inject
    the task's held-out tests, run ``oracle_cmd`` there.

    Returns ``(resolved, output, grade_dir)``.

    Why a rebuild rather than grading the episode's own directory: the agent
    worked in ``workdir``, and anything it did there — a stray file, a
    ``__pycache__`` hit, an edit that only looks right in that conversation —
    would ride along. Rebuilding from the pristine repo with nothing but the
    recorded writes means ``resolved`` is a statement about the patch.

    The patch is complete by construction: ``ToolExecutor`` exposes exactly
    ``list_files`` / ``read_file`` / ``grep`` / ``run_tests`` / ``write_file``
    / ``finish``, with no shell and no delete, so ``write_file`` is the only
    channel that can create or change a file.
    """
    patch: dict[str, str] = {}
    for s in steps:
        if s.tool == "write_file" and s.ok and s.write is not None:
            patch[s.write["path"]] = s.write["content"]  # last write wins

    grade_dir = workdir.parent / f"{workdir.name}-grade"
    if grade_dir.exists():
        shutil.rmtree(grade_dir)
    shutil.copytree(task.repo_dir, grade_dir, ignore=_COPY_IGNORE)

    # Re-resolve every path through the same containment + read-only gate the
    # agent's writes went through, rather than trusting the recorded string.
    gate = ToolExecutor(grade_dir, task.oracle_cmd, task.test_timeout_s)
    for rel, content in patch.items():
        try:
            target = gate._resolve(rel)
        except ValueError as exc:
            return False, f"ERROR: graded patch path rejected: {exc}", grade_dir
        parts = target.relative_to(gate.workdir).parts
        if "tests" in parts or any(
            parts[: len(Path(d).parts)] == Path(d).parts for d in gate.test_paths
        ):
            return False, f"ERROR: graded patch touches a read-only test path: {rel}", grade_dir
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    if task.oracle_dir is not None:
        # Fails loudly when a task ships no hidden tests: that's an authoring
        # error --verify-tasks catches before any paid run.
        shutil.copytree(task.oracle_dir / "tests", grade_dir / "tests")

    resolved, output = ToolExecutor(grade_dir, task.oracle_cmd,
                                    task.test_timeout_s).run_tests()
    return resolved, output, grade_dir


def run_episode(
    task: Task,
    *,
    arm: str,                     # "baseline" | "hive"
    backend: Any,
    stack: Any | None,
    max_turns: int,
    max_tokens: int,
    workdir: Path,
    log_fh: Any | None = None,
    pass_idx: int = 0,
    temperature: float = 0.0,
) -> AgentResult:
    """Run one real episode: real tools, real LLM, real pytest resolve."""
    uses_hive = arm_uses_hive(arm) and stack is not None
    executor = ToolExecutor(workdir, task.test_cmd, task.test_timeout_s)
    shutil.copytree(task.repo_dir, workdir, dirs_exist_ok=True, ignore=_COPY_IGNORE)

    # Integrity gate: the task must actually be broken before the agent runs.
    # A suite that already passes makes "resolved" unmeasurable — hard-fail
    # the episode and name the task, rather than warn and count it anyway.
    # For a held-out task the gate is the *hidden* suite, run against a
    # pristine rebuild with an empty patch — the same path grading takes, so
    # the check cannot be vacuous. (The smoke suite the agent can see passes
    # on a fresh checkout by design and could never serve as this gate.)
    if task.oracle_dir is not None:
        gate_cmd = task.oracle_cmd
        pre_passed, _, _ = grade_patch(task, [], workdir=workdir)
    else:
        gate_cmd = task.test_cmd
        pre_passed, _ = executor.run_tests()
    pre_failed = not pre_passed
    if pre_passed:
        raise TaskIntegrityError(
            f"task {task.id}: {gate_cmd!r} passes on a fresh "
            "checkout — the resolve gate is meaningless for this task"
        )

    state: dict[str, Any] = {
        "goal": task.problem_statement,
        "listed": False,
        "tests_run": 0,
        "tests_passed": None,
        "writes": 0,
        "files_read": [],
        "suggested_read": None,
    }

    # Causal-memory recall (hive arm only). Exact-task recall supplies a
    # replayable patch (zero LLM calls on repeats); family recall supplies a
    # hint for the one reasoning call.
    memory_hit = False
    memory_hint = ""
    if uses_hive:
        exact = stack.recall(f"fix:{task.id}")
        if exact and exact.get("content"):
            state["recalled_fix"] = exact
            memory_hit = True
        else:
            prior = stack.recall(f"fix:{task.family}")
            if prior:
                memory_hit = True
                memory_hint = (
                    f"\nMemory recall — a previous '{task.family}' task was fixed. "
                    f"Details: {json.dumps(prior)[:600]}\n"
                )
    state["memory_hit"] = memory_hit

    # Held-out tasks ship a visible smoke suite that passes before the fix.
    # Saying so is not a hint — it stops the agent from reading green smoke
    # output as "already fixed" and finishing without doing the work.
    smoke_note = (
        "\nThe repo ships a smoke suite (`smoke/`) covering the public API "
        "happy path; it passes before and after the fix and is NOT the "
        "criterion. Fix the source so the behaviour described above holds."
        if task.oracle_dir is not None else ""
    )

    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"ISSUE:\n{task.problem_statement}\n\n"
            f"The repo is mounted at the working directory.{smoke_note}{memory_hint}"
            "Emit your first action."
        )},
    ]

    llm_calls = prompt_tokens = completion_tokens = 0
    obs_chars = ctx_chars = 0
    steps: list[StepLog] = []
    pending_tool_call_id: str | None = None
    t0 = time.perf_counter()
    done = False

    for turn in range(max_turns):
        state["step"] = turn
        decision_state = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
                          for k, v in state.items()}

        # --- decide next action ------------------------------------------
        if uses_hive:
            decision = stack.route(state)
            if decision.escalated:
                # Orchestration: tell the model what the policy already
                # gathered so it spends the call on reasoning, not re-reads.
                escalation_note = (
                    "CONTEXT READY — the failing test output and the unit "
                    "under test are already in this conversation. If you can "
                    "produce the fix, emit write_file now; otherwise take the "
                    "single most useful action."
                )
                messages.append({"role": "user", "content": escalation_note})
                resp = chat_with_retry(backend, messages, max_tokens=max_tokens,
                                       temperature=temperature)
                llm_calls += 1
                prompt_tokens += resp.prompt_tokens
                completion_tokens += resp.completion_tokens
                tool, args = parse_action(resp.text, resp.tool_calls)
                if resp.tool_calls:
                    first_call = resp.tool_calls[:1]
                    pending_tool_call_id = first_call[0].get("id")
                    messages.append({"role": "assistant", "content": resp.text,
                                     "tool_calls": first_call})
                else:
                    pending_tool_call_id = None
                    messages.append({"role": "assistant", "content": resp.text})
                source = "llm"
            else:
                tool, args = decision.tool, {k: str(v) for k, v in decision.args.items()}
                source = "policy"
        else:
            resp = chat_with_retry(backend, messages, max_tokens=max_tokens,
                                   temperature=temperature)
            llm_calls += 1
            prompt_tokens += resp.prompt_tokens
            completion_tokens += resp.completion_tokens
            tool, args = parse_action(resp.text, resp.tool_calls)
            if resp.tool_calls:
                first_call = resp.tool_calls[:1]
                pending_tool_call_id = first_call[0].get("id")
                messages.append({"role": "assistant", "content": resp.text,
                                 "tool_calls": first_call})
            else:
                pending_tool_call_id = None
                messages.append({"role": "assistant", "content": resp.text})
            source = "driver" if isinstance(backend, ScriptedDriver) else "llm"

        # --- execute ------------------------------------------------------
        label = None
        if tool is None:
            observation = "INVALID ACTION — reply with exactly one ACTION: block."
        elif tool == "finish":
            done = True
            observation = "finished"
        elif tool == "list_files":
            observation = executor.list_files()
            state["listed"] = True
        elif tool == "read_file":
            path = args.get("path", "")
            observation = executor.read_file(path)
            if not observation.startswith("ERROR"):
                files = state["files_read"]
                if path not in files:
                    files.append(path)
                if path == state.get("suggested_read"):
                    state["suggested_read"] = None  # suggestion consumed
                # A test file reveals its unit under test via imports —
                # reading that module next is mechanical, not reasoning.
                if "test" in Path(path).name:
                    src = _imports_under_test(observation, executor.workdir)
                    if src and src not in files:
                        state["suggested_read"] = src
        elif tool == "grep":
            observation = executor.grep(args.get("pattern", ""))
        elif tool == "run_tests":
            passed, observation = executor.run_tests()
            state["tests_run"] += 1
            state["tests_passed"] = passed
            state["verify_pending"] = False
            if not passed:
                state["suggested_read"] = _extract_suggested_read(observation, workdir)
                sig = next((ln.strip() for ln in observation.splitlines()
                            if ln.strip().startswith("E ")), "")
                state["fail_signature"] = sig[:200]
        elif tool == "write_file":
            observation = executor.write_file(args.get("path", ""), args.get("content", ""))
            if not observation.startswith("ERROR"):
                state["writes"] += 1
                state["tests_passed"] = None
                state["verify_pending"] = True  # one mechanical verify, then re-diagnose
                state["last_write"] = {"path": args.get("path", ""),
                                       "content": args.get("content", "")}
        else:
            observation = f"ERROR: unknown tool {tool!r}"

        state["last_tool"] = tool or "invalid"
        # Did the tool call actually succeed? ``grade_patch`` replays only the
        # writes that landed, so a rejected write must not enter the patch.
        ok = not observation.startswith(("ERROR", "INVALID"))
        if log_fh is not None:
            from hive.cpu_policy import trajectory_row

            log_fh.write(json.dumps(
                {"task": task.id, "arm": arm, "pass": pass_idx,
                 **trajectory_row(decision_state, tool or "invalid", ok)}
            ) + "\n")
            log_fh.flush()

        observation = _truncate(observation)
        obs_chars += len(observation)

        # --- feed observation back into context ---------------------------
        if uses_hive and tool not in (None, "finish"):
            turn_out = stack.compress("tool", observation)
            ctx_text = turn_out.content
            label = turn_out.label
        else:
            ctx_text = observation
        ctx_chars += len(ctx_text)
        obs_text = f"OBSERVATION[{tool or 'invalid'}]:\n{ctx_text}\n\nNext action."
        if pending_tool_call_id:
            # Tool-call protocol: the result answers the assistant's tool_call.
            messages.append({"role": "tool", "tool_call_id": pending_tool_call_id,
                             "content": obs_text})
            pending_tool_call_id = None
        else:
            messages.append({"role": "user", "content": obs_text})
        steps.append(StepLog(
            turn=turn, decision_source=source, tool=tool or "invalid",
            args={k: (s[:120] + "…" if len(s) > 120 else s)
                  for k, v in args.items() for s in (str(v),)},
            observation_bytes=len(observation), context_bytes=len(ctx_text),
            compress_label=label, ok=ok,
            write=({"path": args.get("path", ""), "content": args.get("content", "")}
                   if ok and tool == "write_file" else None),
        ))
        _log.info("%s/%s turn %d: %s (%s)", task.id, arm, turn, tool, source)

        if done:
            break

    # --- real resolve check -----------------------------------------------
    # Held-out tasks are graded from a pristine rebuild of the repo plus the
    # episode's writes, with tests the agent never saw. Legacy tasks (no
    # oracle_dir) keep the original behaviour: grade the episode workdir.
    if task.oracle_dir is not None:
        resolved, _final_out, grade_dir = grade_patch(task, steps, workdir=workdir)
        _log.info("%s/%s graded in %s -> resolved=%s", task.id, arm, grade_dir, resolved)
    else:
        resolved, _final_out = executor.run_tests()
    if resolved:
        state["tests_passed"] = True

    # Record the fix in causal memory for later family recall — including the
    # failing-assert signature and an excerpt of the patch, so recall is an
    # actual fix pattern, not just a filename.
    if uses_hive and resolved:
        writes = [s.args.get("path") for s in steps if s.tool == "write_file"]
        last_write = state.get("last_write") or {}
        # Exact-task fix: replayable on repeats (CPU writes it, zero LLM calls).
        stack.remember(
            f"fix:{task.id}",
            {"path": last_write.get("path", ""), "content": last_write.get("content", "")},
            tags=("bench", task.family, "replay"),
        )
        stack.remember(
            f"fix:{task.family}",
            {
                "task": task.id,
                "files_written": writes,
                "fail_signature": state.get("fail_signature", ""),
                "fix_excerpt": (last_write.get("content") or "")[:400],
                "resolved": True,
            },
            tags=("bench", task.family),
        )

    return AgentResult(
        task_id=task.id, arm=arm, resolved=resolved, pre_failed=pre_failed,
        pass_idx=pass_idx, held_out=task.oracle_dir is not None,
        turns=len(steps), llm_calls=llm_calls,
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        wall_clock_s=time.perf_counter() - t0, memory_hit=memory_hit,
        observation_chars=obs_chars, context_chars=ctx_chars, steps=steps,
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def per_pass_means(
    results: list[AgentResult], arm: str, metric: str = "llm_calls"
) -> dict[int, float]:
    """Mean of ``metric`` per pass index — the unit a repeat actually varies.

    Steps inside one episode and episodes inside one pass are not independent,
    so the pass is the honest unit for dispersion: report per-pass means, not a
    stderr over 10 tasks, and not a single pooled mean over N passes.
    """
    out: dict[int, float] = {}
    for p in sorted({r.pass_idx for r in results if r.arm == arm}):
        rows = [r for r in results if r.arm == arm and r.pass_idx == p]
        if rows:
            out[p] = sum(getattr(r, metric) for r in rows) / len(rows)
    return out


def dispersion(values: list[float]) -> dict[str, Any]:
    """Mean, sample stderr and range over per-pass values (needs >= 2 passes)."""
    n = len(values)
    if n == 0:
        return {"passes": 0}
    mean = sum(values) / n
    if n < 2:
        # A single pass cannot carry a stderr; say so instead of implying one.
        return {"passes": 1, "mean": round(mean, 2), "stderr": None,
                "min": round(values[0], 2), "max": round(values[0], 2)}
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return {
        "passes": n,
        "mean": round(mean, 2),
        "stderr": round((var / n) ** 0.5, 3),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
    }


def task_grid(results: list[AgentResult], arm: str) -> dict[str, list[bool]]:
    """``{task_id: [resolved, …]}`` in pass order — the grid the paired tests
    and pass^k need, and the grid a reader can recompute every statistic from."""
    grid: dict[str, list[bool]] = {}
    for r in sorted((r for r in results if r.arm == arm), key=lambda r: r.pass_idx):
        grid.setdefault(r.task_id, []).append(bool(r.resolved))
    return grid


def pass_hat_k(results: list[AgentResult], arm: str, k: int) -> float | None:
    """Unbiased pass^k — mean over tasks of ``C(resolved, k) / C(n, k)``.

    The probability that *all* k independent attempts on one task succeed,
    estimated from the repeats actually run. Tasks with fewer than k repeats
    are skipped (not counted as failures); ``None`` when no task qualifies.
    """
    if k < 1:
        raise ValueError("k must be >= 1")
    values: list[float] = []
    for outcomes in task_grid(results, arm).values():
        n = len(outcomes)
        if n < k:
            continue
        values.append(math.comb(sum(outcomes), k) / math.comb(n, k))
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def mcnemar_exact(a: list[bool], b: list[bool]) -> dict[str, Any]:
    """Exact McNemar test on paired outcomes — the honest paired test here.

    Repeats inside a task are correlated, so the paired unit is the *task*, and
    the test asks only about task pairs where the two arms disagree. ``p`` is
    the exact two-sided binomial over the discordant pairs (no chi-square
    approximation, no scipy).
    """
    if len(a) != len(b):
        raise ValueError("paired test needs equal-length outcome lists")
    both = sum(1 for x, y in zip(a, b, strict=True) if x and y)
    neither = sum(1 for x, y in zip(a, b, strict=True) if not x and not y)
    a_only = sum(1 for x, y in zip(a, b, strict=True) if x and not y)
    b_only = sum(1 for x, y in zip(a, b, strict=True) if y and not x)
    n = a_only + b_only
    if n == 0:
        p = 1.0
    else:
        k = min(a_only, b_only)
        p = min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)
    return {"both": both, "neither": neither, "a_only": a_only, "b_only": b_only,
            "discordant": n, "p": round(p, 6)}


def verdict(arm_a: dict[str, Any], arm_b: dict[str, Any], mcnemar: dict[str, Any]) -> str:
    """One of ``separated`` / ``at_ceiling`` / ``not_separable``.

    A null is a result: ``at_ceiling`` means the suite cannot tell the arms
    apart because both solve everything, ``not_separable`` means the observed
    difference is inside what this n can resolve. Neither is a win.
    """
    rate_a, rate_b = arm_a.get("resolve_rate", 0.0), arm_b.get("resolve_rate", 0.0)
    if rate_a == 1.0 and rate_b == 1.0:
        return "at_ceiling"
    ci_a = arm_a.get("resolve_ci95") or [0.0, 1.0]
    ci_b = arm_b.get("resolve_ci95") or [0.0, 1.0]
    disjoint = ci_a[1] < ci_b[0] or ci_b[1] < ci_a[0]
    if mcnemar.get("p", 1.0) < 0.05 and disjoint:
        return "separated"
    return "not_separable"


def summarize(results: list[AgentResult], arm: str, *, price_in: float = 0.22,
              price_out: float = 0.66) -> dict[str, Any]:
    rows = [r for r in results if r.arm == arm]
    if not rows:
        return {}
    n = len(rows)
    resolved = sum(r.resolved for r in rows)
    grid = task_grid(results, arm)
    prompt_tokens = sum(r.prompt_tokens for r in rows)
    completion_tokens = sum(r.completion_tokens for r in rows)
    usd_total = (prompt_tokens * price_in + completion_tokens * price_out) / 1e6
    summary = {
        "arm": arm,
        "tasks": n,
        "resolved": resolved,
        "resolve_rate": round(resolved / n, 4),
        "resolve_ci95": list(wilson(resolved, n)),
        "mean_turns": round(sum(r.turns for r in rows) / n, 2),
        "mean_llm_calls": round(sum(r.llm_calls for r in rows) / n, 2),
        "mean_prompt_tokens": round(prompt_tokens / n, 1),
        "mean_completion_tokens": round(completion_tokens / n, 1),
        "mean_wall_clock_s": round(sum(r.wall_clock_s for r in rows) / n, 2),
        "memory_hits": sum(r.memory_hit for r in rows),
        "total_observation_chars": sum(r.observation_chars for r in rows),
        "total_context_chars": sum(r.context_chars for r in rows),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "usd_total": round(usd_total, 6),
        "usd_per_resolved_task": round(usd_total / resolved, 6) if resolved else 0.0,
        "per_task_resolved": {t: f"{sum(v)}/{len(v)}" for t, v in grid.items()},
        "held_out_episodes": sum(r.held_out for r in rows),
        "tasks_with_oracle": len({r.task_id for r in rows if r.held_out}),
    }
    # pass^k over this arm's repeats (k = the number of passes actually run).
    passes = len({r.pass_idx for r in rows})
    summary["passes"] = passes
    summary["pass_hat_k"] = {str(k): pass_hat_k(results, arm, k) for k in range(1, passes + 1)}
    # Dispersion over passes, so a single-run table cannot be read as a point
    # estimate with a known spread. `passes == 1` means "not measurable here".
    summary["per_pass_mean_llm_calls"] = {
        str(p): round(v, 2) for p, v in per_pass_means(results, arm).items()
    }
    summary["llm_calls_dispersion"] = dispersion(
        list(per_pass_means(results, arm).values())
    )
    return summary


def compare_arms(results: list[AgentResult], arms: list[str],
                 *, price_in: float = 0.22, price_out: float = 0.66) -> dict[str, Any]:
    """Every arm pair present, as an exact paired test plus a plain verdict.

    The paired unit is the task (the mean of its repeats), because repeats
    inside one task are not independent draws — so the discordant counts are
    tasks, not episodes, and the suite's effective n is the number of tasks.
    """
    summaries = {a: summarize(results, a, price_in=price_in, price_out=price_out)
                 for a in arms}
    out: dict[str, Any] = {}
    for i, a in enumerate(arms):
        for b in arms[i + 1:]:
            grid_a, grid_b = task_grid(results, a), task_grid(results, b)
            shared = sorted(set(grid_a) & set(grid_b))
            if not shared:
                continue
            vec_a = [sum(grid_a[t]) * 2 > len(grid_a[t]) for t in shared]
            vec_b = [sum(grid_b[t]) * 2 > len(grid_b[t]) for t in shared]
            mcn = mcnemar_exact(vec_a, vec_b)
            out[f"{a}_vs_{b}"] = {
                "tasks": len(shared),
                "unit": "task (majority of repeats)",
                **mcn,
                "verdict": verdict(summaries[a], summaries[b], mcn),
            }
    return out


def _print_report(results: list[AgentResult], arms: list[str] | None = None,
                  *, price_in: float = 0.22, price_out: float = 0.66) -> None:
    arms = arms or sorted({r.arm for r in results})
    print(f"\n{'task':<24} {'arm':<9} {'pass':<5} {'resolved':<9} {'turns':<6} {'llm_calls':<10} "
          f"{'prompt_tok':<11} {'compl_tok':<10} {'mem':<4} {'sec':<7}")
    for r in results:
        print(f"{r.task_id:<24} {r.arm:<9} {r.pass_idx:<5} {r.resolved!s:<9} {r.turns:<6} "
              f"{r.llm_calls:<10} {r.prompt_tokens:<11} {r.completion_tokens:<10} "
              f"{r.memory_hit!s:<4} {r.wall_clock_s:<7.1f}")
    for arm in arms:
        for p in sorted({r.pass_idx for r in results if r.arm == arm}):
            s = summarize([r for r in results if r.pass_idx == p], arm)
            if s:
                print(f"\n[{arm} pass {p}] resolve={s['resolved']}/{s['tasks']} "
                      f"({s['resolve_rate']*100:.0f}%) llm_calls={s['mean_llm_calls']} "
                      f"prompt_tok={s['mean_prompt_tokens']} turns={s['mean_turns']} "
                      f"mem_hits={s['memory_hits']} "
                      f"ctx_chars={s['total_context_chars']}/{s['total_observation_chars']}")
        s = summarize(results, arm, price_in=price_in, price_out=price_out)
        if not s:
            continue
        print(f"[{arm} all passes] resolve={s['resolved']}/{s['tasks']} "
              f"({s['resolve_rate']*100:.0f}%) ci95={s['resolve_ci95']} "
              f"pass_hat_k={s['pass_hat_k']} "
              f"usd=${s['usd_total']:.4f} usd/resolved=${s['usd_per_resolved_task']:.4f}")
        print(f"[{arm} per task] {s['per_task_resolved']}")
        disp = s.get("llm_calls_dispersion", {})
        if disp.get("passes", 0) >= 2:
            print(f"[{arm} across passes] llm_calls mean={disp['mean']} "
                  f"stderr={disp['stderr']} range={disp['min']}..{disp['max']} "
                  f"({disp['passes']} passes)")
        elif disp.get("passes") == 1:
            print(f"[{arm} across passes] 1 pass — no dispersion measurable; "
                  f"run with --repeat N>1 to report spread")
    for pair, cmp in compare_arms(results, arms, price_in=price_in,
                                  price_out=price_out).items():
        print(f"verdict: {pair} {cmp['verdict']} "
              f"(tasks={cmp['tasks']} a_only={cmp['a_only']} b_only={cmp['b_only']} "
              f"both={cmp['both']} mcnemar_p={cmp['p']})")

# ---------------------------------------------------------------------------
# Task self-check (--verify-tasks): no LLM, no cost
# ---------------------------------------------------------------------------


def _apply_patch(patch_text: str, cwd: Path) -> tuple[bool, str]:
    """Apply a unified diff. ``git apply`` first, ``patch -p1`` as fallback."""
    git = shutil.which("git")
    if git:
        out = subprocess.run([git, "apply", "--unsafe-paths", "-p1"], cwd=cwd,
                             input=patch_text, capture_output=True, text=True)
        if out.returncode == 0:
            return True, ""
        first = (out.stderr or out.stdout).strip().splitlines()[:1]
        reason = first[0] if first else "git apply failed"
    else:
        reason = "git not found"
    patch_bin = shutil.which("patch")
    if not patch_bin:
        return False, f"{reason}; patch(1) not found either"
    out = subprocess.run([patch_bin, "-p1", "--forward"], cwd=cwd, input=patch_text,
                         capture_output=True, text=True)
    return out.returncode == 0, (out.stderr or out.stdout).strip()


def _verify_one(task: Task, scratch: Path, failures: list[str]) -> int:
    """Check one task; append problems to ``failures``. Returns 1 if held out.

    For a held-out task: the hidden suite must FAIL on the pristine repo, PASS
    once ``solution.patch`` is applied, and must not be visible in the
    agent-facing repo. The visible smoke suite must PASS on the pristine repo
    (if it failed, the defect would leak through its traceback and the task
    would be a locate-the-failing-test exercise rather than a reasoning task).

    Tasks with no ``oracle_dir`` (the original suite, graded in the workdir) get
    the broken check only; their oracle/hidden checks are reported as ``skip``
    because there is nothing held out to check.
    """
    workdir = scratch / task.id
    if task.oracle_dir is None:
        # Original suite: grade the workdir, so check the workdir gate.
        workdir.mkdir(parents=True)
        shutil.copytree(task.repo_dir, workdir, dirs_exist_ok=True, ignore=_COPY_IGNORE)
        passed, _out = ToolExecutor(workdir, task.test_cmd, task.test_timeout_s).run_tests()
        broken = not passed
        if not broken:
            failures.append(f"{task.id}: test_cmd passes on a fresh checkout")
        print(f"{task.id} smoke=skip broken={'ok' if broken else 'FAIL'} "
              f"oracle=skip hidden=skip")
        return 0

    # The visible smoke suite must be green before the fix, or the task
    # degenerates into "find the failing test".
    smoke_ok, smoke_out = ToolExecutor(
        task.repo_dir, task.test_cmd, task.test_timeout_s).run_tests()
    if not smoke_ok:
        tail = (smoke_out.strip().splitlines() or ["(no output)"])[-1]
        failures.append(f"{task.id}: smoke suite fails on the pristine repo: {tail}")

    pre_passed, _, _ = grade_patch(task, [], workdir=workdir)
    broken = not pre_passed
    if not broken:
        failures.append(f"{task.id}: hidden oracle passes on the pristine repo")

    hidden_ok = not (task.repo_dir / "tests").exists()
    if not hidden_ok:
        failures.append(f"{task.id}: repo/ ships a tests/ directory — the "
                        "oracle suite would be visible to the agent")

    fail_ok = False
    if task.solution_patch is None or not task.solution_patch.is_file():
        failures.append(f"{task.id}: no solution_patch — apply direction unverifiable")
    else:
        patched = scratch / f"{task.id}-patched"
        shutil.copytree(task.repo_dir, patched, ignore=_COPY_IGNORE)
        applied, why = _apply_patch(task.solution_patch.read_text(), patched)
        if not applied:
            failures.append(f"{task.id}: solution_patch did not apply: {why}")
        else:
            patched_task = dataclasses.replace(task, repo_dir=patched)
            resolved, out, _ = grade_patch(patched_task, [], workdir=workdir)
            fail_ok = resolved
            if not fail_ok:
                tail = (out.strip().splitlines() or ["(no output)"])[-1]
                failures.append(f"{task.id}: oracle still fails after "
                                f"solution_patch: {tail}")
    print(f"{task.id} smoke={'ok' if smoke_ok else 'FAIL'} "
          f"broken={'ok' if broken else 'FAIL'} "
          f"oracle={'ok' if fail_ok else 'FAIL'} "
          f"hidden={'ok' if hidden_ok else 'FAIL'} "
          f"({task.oracle_cmd!r})")
    return 1


def verify_tasks(tasks: list[Task]) -> int:
    scratch = Path(tempfile.mkdtemp(prefix="hive-bench-verify-"))
    failures: list[str] = []
    verified = 0
    try:
        for task in tasks:
            try:
                verified += _verify_one(task, scratch, failures)
            except Exception as exc:  # a broken task is a FAIL, not a crash
                failures.append(f"{task.id}: {type(exc).__name__}: {exc}")
                print(f"{task.id} smoke=FAIL broken=FAIL oracle=FAIL hidden=FAIL "
                      f"({type(exc).__name__}: {exc})")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    if not verified:
        print("verify-tasks: no held-out task in the selection — nothing was checked")
        return 1
    for problem in failures:
        print(f"FAIL\t{problem}")
    print(f"verify-tasks: {verified} held-out task(s) checked, {len(failures)} problem(s)")
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def _git_sha() -> tuple[str | None, bool | None]:
    """(HEAD sha, dirty?) for the runner's checkout, or (None, None) when git
    isn't available — recorded honestly rather than guessed."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT,
            capture_output=True, text=True, timeout=10,
        )
        if sha.returncode != 0:
            return None, None
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=_REPO_ROOT,
            capture_output=True, text=True, timeout=10,
        )
        return sha.stdout.strip(), bool(dirty.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        return None, None


def build_provenance(*, args: Any, stack: Any | None, policy: Any | None,
                     arms: list[str], stacks: dict[str, Any] | None = None,
                     tasks: list[Task] | None = None) -> dict[str, Any]:
    """Record what actually produced the artifact.

    ``--policy`` names the *requested* policy; the class recorded here is the
    concrete object ``stack.route()`` queries (``stack.busybee``), so a silent
    fallback can't masquerade as the trained router in a published artifact.
    ``arm_policy_class`` does the same per arm, because the arms no longer
    share one policy (the ``context`` arm routes nothing by design).
    ``git_sha``/``git_dirty`` are None when the checkout isn't a git repo.
    """
    sha, dirty = _git_sha()
    stacks = stacks or ({arms[-1]: stack} if stack is not None else {})
    queried = getattr(stack, "busybee", None) if stack is not None else None
    if queried is None:
        queried = policy
    hive_arm = "hive" in arms
    task_ids = [t.id for t in tasks] if tasks else []
    solutions_public_since = None
    if tasks:
        dates = [t.solutions_public_since for t in tasks if t.solutions_public_since]
        solutions_public_since = max(dates) if dates else None
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git_sha": sha,
        "git_dirty": dirty,
        "policy": args.policy if hive_arm else None,
        "policy_path": args.policy_path if hive_arm else None,
        "policy_class": type(queried).__name__ if queried is not None else None,
        "temperature": args.temperature,
        "repeat": args.repeat,
        "memory_mode": getattr(args, "memory", None),
        "price_in": getattr(args, "price_in", None),
        "price_out": getattr(args, "price_out", None),
        "arm_specs": {a: {k: v for k, v in ARM_SPECS.get(a, {}).items()} for a in arms},
        "arm_policy_class": {
            a: (type(getattr(s, "busybee", None)).__name__
                if getattr(s, "busybee", None) is not None else None)
            for a, s in stacks.items()
        },
        "task_ids": task_ids,
        "suite_path": str(getattr(args, "suite", "")),
        "tasks_with_oracle": sum(1 for t in (tasks or []) if t.oracle_dir is not None),
        "solutions_public_since": solutions_public_since,
    }




# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", default=str(_REPO_ROOT / "benchmarks" / "tasks"))
    ap.add_argument("--tasks", nargs="*", default=None, help="task ids to run (default: all)")
    ap.add_argument("--arm", choices=["both", "context", "baseline", "hive", "all"],
                    default="both",
                    help="'both'=baseline+hive (default), 'all'=baseline+context+hive; "
                         "'context' isolates compression+memory from routing")
    ap.add_argument("--backend", default="openai", choices=["openai", "vllm", "llama.cpp", "echo"])
    ap.add_argument("--endpoint", default=None, help="OpenAI-compatible base URL (no /v1 suffix)")
    ap.add_argument("--api-key-env", default="OPENAI_API_KEY", help="env var holding the API key")
    ap.add_argument("--model", default="openai/gpt-oss-120b")
    ap.add_argument("--driver", choices=["llm", "scripted"], default="llm",
                    help="'scripted' exercises plumbing without an LLM (resolve rate is meaningless)")
    ap.add_argument("--max-turns", type=int, default=25)
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--temperature", type=float, default=0.0,
                    help="sampling temperature for LLM calls (recorded in the artifact)")
    ap.add_argument("--policy", choices=["rule", "trained"], default="rule",
                    help="CPU routing policy for the hive arm")
    ap.add_argument("--policy-path", default=None,
                    help="joblib file for --policy trained (see scripts/train_cpu_policy.py)")
    ap.add_argument("--log", default=None,
                    help="JSONL file to log every (state -> action) turn for policy training")
    ap.add_argument("--repeat", type=int, default=1,
                    help="run the suite N times on one brain — pass 2 exercises memory replay")
    ap.add_argument("--memory", choices=["fresh", "shared"], default="fresh",
                    help="'fresh' (default) builds a HiveStack per pass, so repeats are "
                         "independent; 'shared' keeps one brain per arm, so pass 2+ is a "
                         "memory-replay measurement, not a capability measurement")
    ap.add_argument("--price-in", type=float, default=0.22,
                    help="USD per 1M prompt tokens (Fireworks DeepSeek V4.1 Flash standard)")
    ap.add_argument("--price-out", type=float, default=0.66,
                    help="USD per 1M completion tokens")
    ap.add_argument("--output", default=None)
    ap.add_argument("--keep-workdirs", action="store_true")
    ap.add_argument("--verify-tasks", action="store_true",
                    help="no-LLM pre-flight: every held-out task must fail on the "
                         "pristine repo and pass with its solution.patch applied")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    tasks = load_tasks(Path(args.suite), args.tasks)
    if not tasks:
        _log.error("no tasks matched")
        return 2

    if args.verify_tasks:
        return verify_tasks(tasks)

    if args.driver == "scripted":
        backend = ScriptedDriver()
    else:
        from hive.llm import make_backend

        endpoint = args.endpoint or os.environ.get("OPENAI_BASE_URL", "").removesuffix("/v1")
        api_key = os.environ.get(args.api_key_env)
        if not endpoint:
            _log.error("pass --endpoint or set OPENAI_BASE_URL")
            return 2
        backend = make_backend("openai", endpoint=endpoint, model=args.model, api_key=api_key)

    arms = arms_for(args.arm)

    if args.policy == "trained" and not args.policy_path and "hive" in arms:
        _log.error("--policy trained requires --policy-path")
        return 2

    # One HiveStack per arm (each arm routes differently), rebuilt per pass
    # when --memory fresh so repeats are independent. ``--memory shared`` keeps
    # one brain per arm: pass 2+ then measures memory replay, not capability.
    # The routing policy object itself is built at most once and shared, so
    # 'rule'/'trained' state can't differ between arms.
    policies: dict[str, Any] = {}
    routing: dict[str, Any] = {}

    def policy_for(arm: str) -> Any:
        if arm in policies:
            return policies[arm]
        if ARM_SPECS[arm]["stack"] == "EscalateOnlyPolicy":
            from hive.harness import EscalateOnlyPolicy

            policies[arm] = EscalateOnlyPolicy()
        else:
            if not routing:
                if args.policy == "trained":
                    from hive.cpu_policy import CPURouterPolicy

                    routing["p"] = CPURouterPolicy.load(args.policy_path)
                    _log.info("trained CPU policy: %s", routing["p"].train_metrics)
                else:
                    from hive.harness import load_routing_policy

                    routing["p"] = load_routing_policy()
            policies[arm] = routing["p"]
        return policies[arm]

    def build_stack_for(arm: str) -> Any | None:
        if not arm_uses_hive(arm):
            return None
        from hive import HiveStack
        from hive.rule_fast import RuleFastHoneyComb
        from hive.rust_brain import RustBrain

        return HiveStack(busybee_policy=policy_for(arm), honey_comb=RuleFastHoneyComb(),
                         rust_brain=RustBrain())

    stacks: dict[str, Any] = {arm: build_stack_for(arm) for arm in arms}
    stack = stacks.get("hive")
    policy = policies.get("hive")

    results: list[AgentResult] = []
    scratch = Path(tempfile.mkdtemp(prefix="hive-bench-"))
    log_fh = open(args.log, "a", encoding="utf-8") if args.log else None
    _log.info("workdirs under %s", scratch)
    try:
        for arm in arms:
            for rep in range(args.repeat):
                if args.memory == "fresh" and rep > 0:
                    stacks[arm] = build_stack_for(arm)
                for task in tasks:
                    workdir = scratch / f"{arm}-p{rep}-{task.id}"
                    workdir.mkdir(parents=True, exist_ok=True)
                    try:
                        result = run_episode(
                            task, arm=arm, backend=backend, stack=stacks[arm],
                            max_turns=args.max_turns, max_tokens=args.max_tokens,
                            workdir=workdir, log_fh=log_fh, pass_idx=rep,
                            temperature=args.temperature,
                        )
                    except Exception:
                        _log.exception("episode %s/%s crashed", task.id, arm)
                        result = AgentResult(
                            task_id=task.id, arm=arm, resolved=False, pre_failed=False,
                            pass_idx=rep, turns=0,
                            llm_calls=0, prompt_tokens=0, completion_tokens=0,
                            wall_clock_s=0.0, memory_hit=False,
                            observation_chars=0, context_chars=0,
                        )
                    results.append(result)
    finally:
        if log_fh is not None:
            log_fh.close()
        if not args.keep_workdirs:
            shutil.rmtree(scratch, ignore_errors=True)

    _print_report(results, arms, price_in=args.price_in, price_out=args.price_out)

    report = {
        "model": args.model,
        "driver": args.driver,
        "suite": str(Path(args.suite)),
        "provenance": build_provenance(args=args, stack=stack, policy=policy,
                                       arms=arms, stacks=stacks, tasks=tasks),
        "results": [dataclasses.asdict(r) for r in results],
        "summary": {arm: summarize(results, arm, price_in=args.price_in,
                                   price_out=args.price_out) for arm in arms},
    }
    report["summary"]["comparisons"] = compare_arms(
        results, arms, price_in=args.price_in, price_out=args.price_out
    )
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2))
        _log.info("wrote %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
