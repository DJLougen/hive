"""external-bench — run hive-bench episodes against an external task manifest.

This is the *machinery* for evaluating Hive on tasks it did not ship with:
a versioned manifest that pins every task's source tree by hash, an overlap
guard against a training inventory, three conditions (model-only baseline,
deterministic rule Hive, trained Hive), metered backend budgets, atomic
incremental results with per-request receipts, and a fail-closed report.

It deliberately reuses ``scripts.hive_bench``'s ``Task`` / ``load_tasks`` /
``run_episode`` unchanged — the episode semantics (real tools, real pytest
resolve, held-out oracle grading) are identical; only the suite definition,
scheduling, metering, and reporting differ.

Nothing here fabricates evidence: a run only counts when every manifest
task x repeat x condition row is present, unique, crash-free, and usage-
recorded. Partial or aborted runs are preserved on disk and are never
reported as complete.

Manifest format (``schema_version: 1``)::

    {
      "schema_version": 1,
      "name": "my-external-suite",
      "tasks": ["task-a", "task-b"],
      "source_sha256": {"task-a": "<64 hex>", "task-b": "<64 hex>"}
    }

Each task id names a directory next to the manifest in the exact layout
``hive_bench.load_tasks`` expects: ``<id>/task.json``, ``<id>/repo/`` and
optionally ``<id>/oracle/``. ``source_sha256`` is the deterministic hash of
the *entire* task directory (see ``hash_task_source``); any change to the
repo, the oracle, or the task metadata fails the run before execution.

Training-inventory file (``--training-inventory``)::

    {"task_ids": ["task-a"], "source_hashes": ["<64 hex>", ...]}

The run refuses to start when either list overlaps the manifest, or when
the file is missing, malformed, or carries no evidence at all.

Usage::

    python scripts/external_bench.py hash-tasks --tasks-dir /path/to/tasks
    python scripts/external_bench.py dry-run --manifest suite.json \
        --training-inventory inventory.json --policy-path policy.joblib
    python scripts/external_bench.py run --manifest suite.json \
        --training-inventory inventory.json --output out/run-1 \
        --model my-model --endpoint-env OPENAI_BASE_URL \
        --api-key-env OPENAI_API_KEY --policy-path policy.joblib \
        --max-requests 500 --max-tokens-total 2000000 --wall-seconds 7200
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_log = logging.getLogger("hive.external_bench")

try:                                  # imported as a module (tests)
    from scripts.hive_bench import AgentResult, Task, load_tasks, run_episode
except ModuleNotFoundError:           # run directly: sys.path[0] is scripts/
    from hive_bench import AgentResult, Task, load_tasks, run_episode

SCHEMA_VERSION = 1
MANIFEST_KEYS = frozenset({"schema_version", "name", "description", "tasks",
                           "source_sha256"})
INVENTORY_KEYS = frozenset({"task_ids", "source_hashes"})

# The three conditions every external run compares. ``arm`` is the
# hive_bench arm the condition maps to; ``policy`` selects the routing
# policy the HiveStack is built with.
CONDITIONS: tuple[str, ...] = ("model-only", "hive-rule", "hive-trained")
CONDITION_ARM: dict[str, str] = {
    "model-only": "baseline",
    "hive-rule": "hive",
    "hive-trained": "hive",
}

# Directory names excluded from source hashing — caches the task itself
# produces, not source content.
_HASH_IGNORE_DIRS = frozenset({"__pycache__", ".pytest_cache", ".git"})


class ExternalBenchError(Exception):
    """Configuration or validation failure — the run never started."""


class RunAbort(BaseException):
    """Abort the whole run, preserving rows already written.

    BaseException, not Exception: hive_bench's ``chat_with_retry`` catches
    ``Exception`` and retries, which would turn an abort into hidden extra
    spend. Nothing in ``run_episode`` catches BaseException, so this
    propagates straight to the run loop.
    """


class BudgetExceeded(RunAbort):
    """A metered budget (requests / tokens / wall seconds) was exceeded."""


class UsageUnknownError(RunAbort):
    """The backend returned a response without usable token usage."""


class BackendError(RunAbort):
    """The backend raised; the run aborts rather than guessing at cost."""


# ---------------------------------------------------------------------------
# Manifest + source hashing
# ---------------------------------------------------------------------------


def _is_sha256(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(c in "0123456789abcdef" for c in value))


def _is_safe_task_id(value: Any) -> bool:
    """A task id must be a single relative path component — no traversal,
    no absolute paths, no separators, no blanks."""
    if not isinstance(value, str) or not value or not value.strip():
        return False
    p = Path(value)
    return (not p.is_absolute() and len(p.parts) == 1
            and value not in (".", ".."))


def hash_task_source(task_dir: Path) -> str:
    """Deterministic sha256 over every file under ``task_dir``.

    Covers ``task.json``, ``repo/`` and ``oracle/`` — a change to any of
    them changes the hash. Cache directories are excluded; empty
    directories carry no content and are not hashed. Any symlink in the
    tree is rejected outright: a link could point outside the pinned
    directory, and hashing resolved content would silently certify files
    the manifest does not own.
    """
    task_dir = Path(task_dir)
    if not task_dir.is_dir():
        raise ExternalBenchError(f"task source directory missing: {task_dir}")
    h = hashlib.sha256()
    entries = sorted(task_dir.rglob("*"))
    for path in entries:
        if _HASH_IGNORE_DIRS & set(path.parts):
            continue
        if path.is_symlink():
            raise ExternalBenchError(
                f"task source contains a symlink: {path} — symlinks escape "
                "the pinned tree and are refused")
        if not path.is_file():
            continue
        rel = path.relative_to(task_dir).as_posix().encode("utf-8")
        content = path.read_bytes()
        # Length-prefix both fields: a bare path\0content\0 join is
        # ambiguous — one file "a" containing "x\0b\0y" hashes identically
        # to files "a"="x" plus "b"="y".
        h.update(len(rel).to_bytes(8, "big"))
        h.update(rel)
        h.update(len(content).to_bytes(8, "big"))
        h.update(content)
    return h.hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _require_str_list(value: Any, what: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ExternalBenchError(f"{what} must be a list of strings")
    return list(value)


def load_manifest(path: Path) -> tuple[list[Task], dict[str, Any]]:
    """Load and strictly validate a versioned external manifest.

    Returns ``(tasks, manifest_info)``. Fails closed on: unreadable or
    non-object JSON, wrong ``schema_version``, unknown keys, empty or
    duplicated task ids, a ``source_sha256`` map that does not cover
    exactly the task list, non-hex hashes, or a task directory that does
    not exist.
    """
    path = Path(path)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExternalBenchError(f"manifest unreadable: {path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ExternalBenchError("manifest must be a JSON object")
    unknown = set(manifest) - MANIFEST_KEYS
    if unknown:
        raise ExternalBenchError(f"manifest has unknown keys: {sorted(unknown)}")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ExternalBenchError(
            f"manifest schema_version must be {SCHEMA_VERSION}, "
            f"got {manifest.get('schema_version')!r}")
    task_ids = _require_str_list(manifest.get("tasks"), "manifest 'tasks'")
    if not task_ids:
        raise ExternalBenchError("manifest 'tasks' is empty")
    unsafe = [t for t in task_ids if not _is_safe_task_id(t)]
    if unsafe:
        raise ExternalBenchError(
            f"manifest task ids must be single relative path components: {unsafe}")
    dupes = sorted({t for t in task_ids if task_ids.count(t) > 1})
    if dupes:
        raise ExternalBenchError(f"manifest lists duplicate task ids: {dupes}")
    hashes = manifest.get("source_sha256")
    if not isinstance(hashes, dict):
        raise ExternalBenchError("manifest 'source_sha256' must be an object "
                                 "mapping task id -> sha256 hex")
    if set(hashes) != set(task_ids):
        raise ExternalBenchError(
            "manifest 'source_sha256' must cover exactly the task list; "
            f"missing={sorted(set(task_ids) - set(hashes))} "
            f"extra={sorted(set(hashes) - set(task_ids))}")
    for tid, digest in hashes.items():
        if not _is_sha256(digest):
            raise ExternalBenchError(
                f"source_sha256[{tid!r}] is not a lowercase sha256 hex digest")

    # task.json must agree with the manifest, and every path it names must
    # stay inside the task directory — an oracle or solution patch outside
    # the pinned tree would be graded from unhashed files.
    for tid in task_ids:
        task_dir = path.parent / tid
        try:
            meta = json.loads((task_dir / "task.json").read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ExternalBenchError(
                f"task {tid!r} has no readable task.json: {exc}") from exc
        if meta.get("id") != tid:
            raise ExternalBenchError(
                f"task {tid!r} task.json declares id {meta.get('id')!r}")
        for key in ("oracle_dir", "solution_patch"):
            rel = meta.get(key)
            if rel is None:
                continue
            resolved = (task_dir / rel).resolve()
            if task_dir.resolve() not in resolved.parents:
                raise ExternalBenchError(
                    f"task {tid!r} {key} escapes the task directory: {rel!r}")

    tasks = load_tasks(path)  # manifest is a superset of suite.json
    by_id = {t.id: t for t in tasks}
    for tid in task_ids:
        task = by_id[tid]
        if not task.repo_dir.is_dir():
            raise ExternalBenchError(f"task {tid!r} has no repo/ directory")
        if task.oracle_dir is not None and not task.oracle_dir.is_dir():
            raise ExternalBenchError(f"task {tid!r} has no oracle/ directory")

    info = {
        "path": str(path),
        "sha256": _sha256_file(path),
        "name": manifest.get("name"),
        "task_ids": task_ids,
        "source_sha256": dict(hashes),
    }
    return tasks, info


def verify_source_hashes(tasks: list[Task], manifest_info: dict[str, Any]) -> None:
    """Re-hash every task directory and compare against the pinned manifest.

    Called once at startup and again before each task's episodes: a source
    that changed mid-run is rejected rather than silently evaluated.
    """
    expected = manifest_info["source_sha256"]
    for task in tasks:
        task_dir = task.repo_dir.parent
        actual = hash_task_source(task_dir)
        if actual != expected[task.id]:
            raise ExternalBenchError(
                f"task {task.id!r} source changed: manifest pins "
                f"{expected[task.id][:12]}… but the tree hashes to "
                f"{actual[:12]}… — refusing to evaluate mutated sources")


# ---------------------------------------------------------------------------
# Training-overlap guard
# ---------------------------------------------------------------------------


def load_training_inventory(path: Path) -> dict[str, list[str]]:
    """Load the training inventory ``{task_ids, source_hashes}``.

    Fail closed: the file must exist, be a JSON object with both keys as
    string lists, and carry at least one entry — an empty inventory is not
    evidence of disjointness, it is no evidence at all.
    """
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExternalBenchError(
            f"training inventory unreadable: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ExternalBenchError("training inventory must be a JSON object")
    unknown = set(raw) - INVENTORY_KEYS
    if unknown:
        raise ExternalBenchError(
            f"training inventory has unknown keys: {sorted(unknown)}")
    task_ids = _require_str_list(raw.get("task_ids"), "inventory 'task_ids'")
    hashes = _require_str_list(raw.get("source_hashes"),
                               "inventory 'source_hashes'")
    bad_ids = [t for t in task_ids if not _is_safe_task_id(t)]
    if bad_ids:
        raise ExternalBenchError(
            f"inventory task_ids must be non-blank safe ids: {bad_ids}")
    bad_hashes = [h for h in hashes if not _is_sha256(h)]
    if bad_hashes:
        raise ExternalBenchError(
            "inventory source_hashes must be lowercase sha256 hex digests: "
            f"{[h[:16] for h in bad_hashes]}")
    if not task_ids and not hashes:
        raise ExternalBenchError(
            "training inventory is empty — an empty inventory is not proof "
            "of disjointness; supply the real task_ids/source_hashes")
    return {"task_ids": task_ids, "source_hashes": hashes}


def check_overlap(manifest_info: dict[str, Any],
                  inventory: dict[str, list[str]]) -> None:
    """Refuse the run when the manifest overlaps the training inventory."""
    id_hits = sorted(set(manifest_info["task_ids"]) & set(inventory["task_ids"]))
    hash_hits = sorted(set(manifest_info["source_sha256"].values())
                       & set(inventory["source_hashes"]))
    if id_hits or hash_hits:
        raise ExternalBenchError(
            "manifest overlaps the training inventory — evaluation would "
            "measure memorization, not capability: "
            f"task_ids={id_hits} source_hashes={[h[:12] + '…' for h in hash_hits]}")


# ---------------------------------------------------------------------------
# Conditions, ordering, stacks
# ---------------------------------------------------------------------------


def condition_order(task_index: int, conditions: list[str]) -> list[str]:
    """Deterministic rotation: task i starts at conditions[i % n].

    Every task runs every condition exactly once, but the order rotates so
    a fixed ordering cannot systematically favour one condition (cache
    warmth, endpoint drift, time-of-day).
    """
    if not conditions:
        raise ExternalBenchError("no conditions selected")
    k = task_index % len(conditions)
    return conditions[k:] + conditions[:k]


def load_trained_policy(policy_path: Path) -> Any:
    """Load the trained routing policy through the existing signature guard.

    ``CPURouterPolicy.load`` refuses unsigned artifacts unless
    ``HIVE_ALLOW_UNSIGNED_MODEL=1`` is set; we never bypass that guard, and
    the report records when the override was in effect.
    """
    from hive.cpu_policy import CPURouterPolicy

    return CPURouterPolicy.load(policy_path)


def build_stack(condition: str, *, rule_policy: Any, trained_policy: Any) -> Any:
    """A fresh HiveStack per episode — memory never carries across episodes.

    The routing policy is deep-copied too: ``CPURouterPolicy.predict``
    stores ``_last_route`` (and the rule policy counts routed/escalated
    stats), so sharing the object would leak routing state between
    episodes even though the brain is fresh.
    """
    if CONDITION_ARM[condition] != "hive":
        return None
    import copy

    from hive import HiveStack
    from hive.rule_fast import RuleFastHoneyComb
    from hive.rust_brain import RustBrain

    template = rule_policy if condition == "hive-rule" else trained_policy
    policy = copy.deepcopy(template)
    return HiveStack(busybee_policy=policy, honey_comb=RuleFastHoneyComb(),
                     rust_brain=RustBrain())


# ---------------------------------------------------------------------------
# Metered backend
# ---------------------------------------------------------------------------


class MeteredBackend:
    """Wrap a hive_bench backend with hard budgets and per-request receipts.

    Budgets are enforced *before* a request is sent, so an exhausted budget
    never produces a billable call. Token headroom is reserved
    conservatively — one token per serialized input byte plus the per-call
    ``max_tokens`` cap — so a call that could overspend is refused rather
    than billed. When the backend exposes a ``timeout`` attribute (the
    OpenAI-compatible HTTP backend does), it is clamped to the remaining
    wall budget; that bounds the socket wait but cannot hard-kill a call
    already in flight — a hung backend can still run past the wall budget
    until its own timeout fires. Every attempt — including ones the
    backend fails — is receipted, so retries inside ``chat_with_retry``
    cannot hide spend. Budget exhaustion, unknown usage, and backend
    errors all raise ``RunAbort`` subclasses (BaseException): they evade
    the harness's ``except Exception`` retry/crash paths by design.
    """

    def __init__(self, backend: Any, *, max_requests: int,
                 max_tokens_total: int, wall_seconds: float,
                 receipt_sink: Any) -> None:
        if not isinstance(max_requests, int) or max_requests <= 0:
            raise ExternalBenchError("max_requests must be a positive int")
        if not isinstance(max_tokens_total, int) or max_tokens_total <= 0:
            raise ExternalBenchError("max_tokens_total must be a positive int")
        if (not isinstance(wall_seconds, (int, float))
                or not math.isfinite(wall_seconds) or wall_seconds <= 0):
            raise ExternalBenchError(
                "wall_seconds must be finite and positive")
        self._backend = backend
        self._max_requests = max_requests
        self._max_tokens = max_tokens_total
        self._wall_seconds = float(wall_seconds)
        self._sink = receipt_sink
        self._t0 = time.monotonic()
        self.requests = 0
        self.metered_responses = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self._ctx: dict[str, Any] = {}

    def set_context(self, *, task_id: str, pass_idx: int, condition: str) -> None:
        self._ctx = {"task_id": task_id, "pass_idx": pass_idx,
                     "condition": condition}

    @property
    def tokens_total(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def usage_summary(self) -> dict[str, Any]:
        """Separate full-run usage from the known subtotal after unknown spend."""
        complete = self.requests == self.metered_responses
        return {
            "requests": self.requests,
            "metered_responses": self.metered_responses,
            "usage_recorded": complete,
            "prompt_tokens": self.prompt_tokens if complete else None,
            "completion_tokens": self.completion_tokens if complete else None,
            "measured_subtotal": {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
            },
        }

    def _check_budget(self) -> None:
        """Pre-call gate: refuse a request that cannot be within budget."""
        if self.requests >= self._max_requests:
            raise BudgetExceeded(
                f"request budget exhausted ({self.requests}/{self._max_requests})")
        if self.tokens_total >= self._max_tokens:
            raise BudgetExceeded(
                f"token budget exhausted "
                f"({self.tokens_total}/{self._max_tokens})")
        if time.monotonic() - self._t0 >= self._wall_seconds:
            raise BudgetExceeded(
                f"wall-clock budget exhausted ({self._wall_seconds}s)")

    def _check_overrun(self) -> None:
        """Post-call gate: a response that landed over budget still aborts —
        the spend is receipted, but the run stops rather than continue."""
        if self.tokens_total > self._max_tokens:
            raise BudgetExceeded(
                f"token budget exceeded "
                f"({self.tokens_total}/{self._max_tokens})")
        if time.monotonic() - self._t0 > self._wall_seconds:
            raise BudgetExceeded(
                f"wall-clock budget exceeded ({self._wall_seconds}s)")

    def chat(self, messages: Any, **kwargs: Any) -> Any:
        self._check_budget()
        # Conservative token reservation: one token per serialized input
        # byte plus the per-call output cap. A call that could overspend
        # the token budget is refused before it is billed.
        input_bytes = len(json.dumps(
            {"messages": messages, **kwargs}, default=str).encode("utf-8")) + 512
        reserve = input_bytes + int(kwargs.get("max_tokens") or 0)
        if self.tokens_total + reserve >= self._max_tokens:
            raise BudgetExceeded(
                f"token budget cannot cover this call "
                f"(used={self.tokens_total} reserve={reserve} "
                f"budget={self._max_tokens})")
        # Clamp a known HTTP backend's socket timeout to the remaining wall
        # budget. This bounds the wait; it cannot hard-kill a call already
        # in flight (see class docstring).
        remaining = self._wall_seconds - (time.monotonic() - self._t0)
        if hasattr(self._backend, "timeout"):
            try:
                self._backend.timeout = max(0.1, min(
                    float(self._backend.timeout), remaining))
            except (TypeError, ValueError):
                pass
        self.requests += 1
        receipt = {"seq": self.requests, "ts": time.time(), **self._ctx}
        try:
            resp = self._backend.chat(messages, **kwargs)
        except RunAbort:
            raise
        except BaseException as exc:  # re-raised as a run abort
            receipt.update(ok=False, error=f"{type(exc).__name__}: {exc}")
            self._sink(receipt)
            raise BackendError(
                f"backend call failed after {self.requests} metered "
                f"request(s): {exc}") from exc
        prompt = getattr(resp, "prompt_tokens", None)
        completion = getattr(resp, "completion_tokens", None)
        # A real prompt always costs tokens; prompt_tokens==0 or a missing
        # field means the endpoint did not report usage, and unpriced usage
        # cannot enter the cost model — abort rather than record a zero.
        if (not isinstance(prompt, int) or isinstance(prompt, bool)
                or not isinstance(completion, int)
                or isinstance(completion, bool)
                or prompt <= 0 or completion <= 0):
            receipt.update(ok=False, error="usage not reported by backend",
                           model=getattr(resp, "model", None))
            self._sink(receipt)
            raise UsageUnknownError(
                "backend response carried no usable token usage "
                f"(prompt_tokens={prompt!r}, completion_tokens={completion!r})")
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.metered_responses += 1
        receipt.update(ok=True, model=getattr(resp, "model", ""),
                       prompt_tokens=prompt, completion_tokens=completion,
                       duration_s=getattr(resp, "duration_s", None))
        self._sink(receipt)
        self._check_overrun()  # a response that lands over budget still aborts
        return resp


# ---------------------------------------------------------------------------
# Incremental writers
# ---------------------------------------------------------------------------


class JsonlWriter:
    """Append-only JSONL with flush+fsync per row — rows survive a crash."""

    def __init__(self, path: Path) -> None:
        self._fh = open(path, "a", encoding="utf-8")

    def write(self, row: dict[str, Any]) -> None:
        self._fh.write(json.dumps(row, default=str) + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def close(self) -> None:
        self._fh.close()


def write_report_atomic(path: Path, report: dict[str, Any]) -> None:
    """Write report.json via tmp+rename so readers never see a torn file."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".report-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(report, indent=2, default=str))
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


# ---------------------------------------------------------------------------
# Reporting — paired outcomes and priced cost, no inference
# ---------------------------------------------------------------------------


def _is_nonneg_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _row_valid(row: dict[str, Any]) -> bool:
    """A row counts only if the episode ran to a verdict with usage —
    and the counters it reports are well-formed (non-bool ints, finite
    non-negative elapsed)."""
    if not (isinstance(row.get("resolved"), bool)
            and not row.get("crashed", False)
            and row.get("usage_recorded", False)):
        return False
    counters = ("prompt_tokens", "completion_tokens", "llm_calls", "turns")
    if not all(_is_nonneg_int(row.get(k)) for k in counters):
        return False
    elapsed = row.get("wall_clock_s")
    return (isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool)
            and math.isfinite(elapsed) and elapsed >= 0)


def _audit_rows(rows: list[dict[str, Any]], tasks: list[Task],
                conditions: list[str], repeat: int) -> list[str]:
    """Fail-closed completeness audit over the recorded rows.

    Returns the list of problems; empty means the run may report
    ``complete``. Any problem — duplicates, unmatched ids, missing cells,
    crashed or usage-less rows — keeps the run unpromotable. An empty
    expected set is itself a problem: nothing planned means nothing was
    verified.
    """
    problems: list[str] = []
    expected = {(t.id, p, c) for t in tasks for p in range(repeat)
                for c in conditions}
    if not expected:
        return ["no episodes planned — nothing was verified"]
    seen: set[tuple[str, int, str]] = set()
    for i, row in enumerate(rows):
        key = (row.get("task_id"), row.get("pass_idx"), row.get("condition"))
        if None in key or key not in expected:
            problems.append(f"row {i}: unmatched identity {key}")
            continue
        if key in seen:
            problems.append(f"row {i}: duplicate episode {key}")
        seen.add(key)
        if row.get("crashed"):
            problems.append(f"row {i}: crashed episode {key}")
        elif not _row_valid(row):
            problems.append(f"row {i}: invalid row {key} "
                            "(missing usage or malformed)")
    missing = sorted(expected - seen)
    for key in missing:
        problems.append(f"missing episode {key}")
    return problems


def summarize_condition(rows: list[dict[str, Any]], condition: str,
                        *, price_in: float, price_out: float) -> dict[str, Any]:
    """Aggregate one condition. Usage fields are null whenever any outcome
    row lacks recorded usage — a partial total must never read as a full
    one. ``usd_per_resolved`` is null at zero resolves: cost/0 is
    undefined, not free."""
    rows_c = [r for r in rows if r.get("condition") == condition]
    valid = [r for r in rows_c if _row_valid(r)]
    out: dict[str, Any] = {
        "condition": condition,
        "episodes": len(rows_c),
        "episodes_valid": len(valid),
        "episodes_crashed": sum(1 for r in rows_c if r.get("crashed")),
        "resolved": sum(1 for r in valid if r["resolved"]),
        "resolve_rate": (round(sum(1 for r in valid if r["resolved"]) / len(valid), 4)
                         if valid else None),
    }
    measured = bool(valid) and len(valid) == len(rows_c)
    if measured:
        prompt = sum(r["prompt_tokens"] for r in valid)
        completion = sum(r["completion_tokens"] for r in valid)
        usd = (prompt * price_in + completion * price_out) / 1e6
        out.update(
            prompt_tokens=prompt,
            completion_tokens=completion,
            llm_calls=sum(r["llm_calls"] for r in valid),
            mean_turns=round(sum(r["turns"] for r in valid) / len(valid), 2),
            mean_wall_clock_s=round(
                sum(r["wall_clock_s"] for r in valid) / len(valid), 2),
            usd_estimated=round(usd, 6),
            usd_per_resolved=(round(usd / out["resolved"], 6)
                              if out["resolved"] else None),
        )
    else:
        out.update(prompt_tokens=None, completion_tokens=None, llm_calls=None,
                   mean_turns=None, mean_wall_clock_s=None,
                   usd_estimated=None, usd_per_resolved=None)
    return out


def paired_grid(rows: list[dict[str, Any]], conditions: list[str],
                *, expected: set[tuple[str, int]] | None = None
                ) -> dict[str, Any]:
    """Per-(task, pass) paired outcomes across conditions — the raw pairing
    a reader recomputes from.

    The pairing unit is the exact ``(task_id, pass_idx)`` cell: a cell is
    paired only when every selected condition produced exactly one row
    for *that* pass and that row is valid. A partial run can therefore
    never pair baseline pass 0 against hive pass 1 — the two cells are
    reported separately as incomplete instead of being silently merged.

    - ``grid``: paired cells only, each ``{task_id, pass_idx, resolved:
      {condition: bool}}``.
    - ``incomplete_cells``: cells where some condition has no valid row —
      including planned cells that produced no rows at all — with the
      resolved values of the conditions that did produce one.
    - ``ambiguous_cells``: cells where a condition produced more than one
      row — duplicates are never collapsed into a pairing, even when only
      one of them is valid.

    ``expected`` is the planned ``{(task_id, pass_idx)}`` universe; pass it
    so a pass that never ran is reported incomplete instead of vanishing
    (and so ``tasks_fully_paired`` cannot read true on a half-run task).
    When supplied, rows whose cell is outside the plan are excluded —
    ``_audit_rows`` reports them as unmatched. Without it only observed
    cells are classified.

    ``tasks_fully_paired`` and ``tasks_all_conditions_resolved`` are
    task-level: a task counts only when every one of its cells is paired
    (and, for the latter, resolved in every condition).

    Rows without a placeable identity (missing task_id/pass_idx, or a
    condition outside the selected set) are excluded here; ``_audit_rows``
    reports them. No significance or equivalence claim is computed here or
    anywhere else in this module.
    """
    # cells[(task_id, pass_idx)][condition] = {"rows": total rows seen,
    #                                          "resolved": [bool per valid row]}
    cells: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    for key in expected or ():
        cells.setdefault(key, {})
    for r in rows:
        task_id, pass_idx, cond = (r.get("task_id"), r.get("pass_idx"),
                                   r.get("condition"))
        if (not isinstance(task_id, str) or not _is_nonneg_int(pass_idx)
                or cond not in conditions):
            continue
        if expected is not None and (task_id, pass_idx) not in expected:
            continue  # unplanned identity — _audit_rows reports it
        entry = cells.setdefault((task_id, pass_idx), {}).setdefault(
            cond, {"rows": 0, "resolved": []})
        entry["rows"] += 1
        if _row_valid(r):
            entry["resolved"].append(bool(r["resolved"]))

    grid: list[dict[str, Any]] = []
    incomplete: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    for (task_id, pass_idx), conds in sorted(cells.items()):
        dup = sorted(c for c in conditions
                     if conds.get(c, {}).get("rows", 0) > 1)
        if dup:
            ambiguous.append({"task_id": task_id, "pass_idx": pass_idx,
                              "duplicated_conditions": dup,
                              "rows": {c: conds[c]["rows"] for c in dup},
                              "resolved": {c: conds[c]["resolved"]
                                           for c in dup}})
            continue
        missing = [c for c in conditions if c not in conds]
        invalid = [c for c in conditions
                   if c in conds and not conds[c]["resolved"]]
        if missing or invalid:
            incomplete.append({"task_id": task_id, "pass_idx": pass_idx,
                               "missing_conditions": missing,
                               "invalid_conditions": invalid,
                               "resolved": {c: conds[c]["resolved"][0]
                                            for c in conditions
                                            if conds.get(c, {})
                                               .get("resolved")}})
            continue
        grid.append({"task_id": task_id, "pass_idx": pass_idx,
                     "resolved": {c: conds[c]["resolved"][0]
                                  for c in conditions}})

    resolved_by_key = {(c["task_id"], c["pass_idx"]): c["resolved"]
                       for c in grid}
    paired_keys = set(resolved_by_key)
    tasks_seen = {k[0] for k in cells}
    tasks_fully_paired = 0
    tasks_all_resolved = 0
    for t in tasks_seen:
        t_cells = [k for k in cells if k[0] == t]
        if all(k in paired_keys for k in t_cells):
            tasks_fully_paired += 1
            if all(all(resolved_by_key[k].values()) for k in t_cells):
                tasks_all_resolved += 1
    return {"unit": "task x pass",
            "cells_paired": len(grid),
            "cells_incomplete": len(incomplete),
            "cells_ambiguous": len(ambiguous),
            "tasks_fully_paired": tasks_fully_paired,
            "tasks_all_conditions_resolved": tasks_all_resolved,
            "grid": grid,
            "incomplete_cells": incomplete,
            "ambiguous_cells": ambiguous}


def _git_sha() -> tuple[str | None, bool | None]:
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT,
                             capture_output=True, text=True, timeout=10)
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=_REPO_ROOT,
                               capture_output=True, text=True, timeout=10)
        if sha.returncode != 0:
            return None, None
        return sha.stdout.strip(), bool(dirty.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        return None, None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _crash_row(task_id: str, pass_idx: int, condition: str, error: str) -> dict:
    row = dataclasses.asdict(AgentResult(
        task_id=task_id, arm=CONDITION_ARM[condition], resolved=False,
        pre_failed=False, pass_idx=pass_idx, turns=0, llm_calls=0,
        prompt_tokens=0, completion_tokens=0, wall_clock_s=0.0,
        memory_hit=False, observation_chars=0, context_chars=0,
        crashed=True, usage_recorded=False))
    row["condition"] = condition
    row["error"] = error
    return row


def _result_row(result: AgentResult, condition: str) -> dict:
    row = dataclasses.asdict(result)
    row["condition"] = condition
    return row


def _check_conditions(conditions: list[str]) -> None:
    """Conditions must be non-empty, unique, and known."""
    if not conditions:
        raise ExternalBenchError("no conditions selected")
    unknown = [c for c in conditions if c not in CONDITION_ARM]
    if unknown:
        raise ExternalBenchError(f"unknown conditions: {unknown}")
    if len(set(conditions)) != len(conditions):
        raise ExternalBenchError(f"duplicate conditions: {conditions}")


def _validate_run_params(*, temperature: float, price_in: float,
                         price_out: float, max_turns: int,
                         max_tokens: int) -> None:
    """Run parameters must be finite and in range before any spend."""
    for name, value in (("temperature", temperature), ("price_in", price_in),
                        ("price_out", price_out)):
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(value) or value < 0):
            raise ExternalBenchError(f"{name} must be finite and non-negative")
    for name, value in (("max_turns", max_turns), ("max_tokens", max_tokens)):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ExternalBenchError(f"{name} must be a positive int")


def run_suite(*, tasks: list[Task], manifest_info: dict[str, Any],
              inventory_info: dict[str, Any], backend: Any,
              conditions: list[str], repeat: int, policy_path: Path | None,
              budgets: dict[str, Any], out_dir: Path,
              max_turns: int, max_tokens: int, temperature: float,
              price_in: float, price_out: float, model: str,
              endpoint: str | None) -> dict[str, Any]:
    """Run every task x repeat x condition, writing results incrementally.

    Returns the report dict (also written atomically to
    ``out_dir/report.json``). ``report["status"]`` is ``complete`` only
    when the audit finds no problems; ``aborted`` when a RunAbort stopped
    the loop; ``partial`` otherwise. Partial and aborted reports are
    evidence of spend, never of capability.
    """
    _check_conditions(conditions)
    _validate_run_params(temperature=temperature, price_in=price_in,
                         price_out=price_out, max_turns=max_turns,
                         max_tokens=max_tokens)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=False)
    scratch = out_dir / "workdirs"
    scratch.mkdir()
    results_writer = JsonlWriter(out_dir / "results.jsonl")
    receipts_writer = JsonlWriter(out_dir / "receipts.jsonl")
    meter = MeteredBackend(
        backend, max_requests=budgets["max_requests"],
        max_tokens_total=budgets["max_tokens_total"],
        wall_seconds=budgets["wall_seconds"],
        receipt_sink=receipts_writer.write)

    # Policies are built once per run; build_stack() deep-copies them per
    # episode so neither the brain nor policy loop state (_last_route,
    # stats counters) carries across episodes.
    rule_policy = trained_policy = None
    if "hive-rule" in conditions:
        from hive.harness import load_routing_policy

        rule_policy = load_routing_policy()  # deterministic rule policy
    if "hive-trained" in conditions:
        trained_policy = load_trained_policy(policy_path)  # signature-guarded

    rows: list[dict[str, Any]] = []
    abort_reason: str | None = None
    try:
        for task_index, task in enumerate(tasks):
            for pass_idx in range(repeat):
                for condition in condition_order(task_index, conditions):
                    # Re-verify before EVERY episode: a source mutated
                    # mid-run aborts rather than evaluating tampered input.
                    try:
                        verify_source_hashes([task], manifest_info)
                    except ExternalBenchError as exc:
                        raise RunAbort(f"SourceChanged: {exc}") from exc
                    workdir = scratch / f"{condition}-p{pass_idx}-{task.id}"
                    workdir.mkdir(parents=True)
                    meter.set_context(task_id=task.id, pass_idx=pass_idx,
                                      condition=condition)
                    try:
                        result = run_episode(
                            task, arm=CONDITION_ARM[condition], backend=meter,
                            stack=build_stack(condition, rule_policy=rule_policy,
                                              trained_policy=trained_policy),
                            max_turns=max_turns, max_tokens=max_tokens,
                            workdir=workdir, pass_idx=pass_idx,
                            temperature=temperature,
                            source_navigation="legacy",
                        )
                        row = _result_row(result, condition)
                    except RunAbort as exc:
                        abort_reason = f"{type(exc).__name__}: {exc}"
                        row = _crash_row(task.id, pass_idx, condition,
                                         abort_reason)
                        rows.append(row)
                        results_writer.write(row)
                        raise
                    except Exception as exc:  # episode crash: record, continue
                        _log.exception("episode %s/%s crashed", task.id, condition)
                        row = _crash_row(task.id, pass_idx, condition,
                                         f"{type(exc).__name__}: {exc}")
                    rows.append(row)
                    results_writer.write(row)
        # Final integrity check: sources must be unchanged at the end too.
        try:
            verify_source_hashes(tasks, manifest_info)
        except ExternalBenchError as exc:
            raise RunAbort(f"SourceChanged: {exc}") from exc
    except RunAbort as exc:
        if abort_reason is None:
            abort_reason = f"{type(exc).__name__}: {exc}"
        _log.error("run aborted: %s", abort_reason)
    finally:
        receipts_writer.close()
        results_writer.close()

    problems = _audit_rows(rows, tasks, conditions, repeat)
    if abort_reason is not None:
        status = "aborted"
    elif problems:
        status = "partial"
    else:
        status = "complete"
    git_sha, git_dirty = _git_sha()
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        # A run is promotable only when every expected episode row exists,
        # is unique, ran to a verdict, and recorded usage.
        "promotable": status == "complete",
        "abort_reason": abort_reason,
        "audit_problems": problems,
        "model": model,
        "endpoint": endpoint,
        "conditions": conditions,
        "repeat": repeat,
        "budgets": dict(budgets),
        "usage_totals": meter.usage_summary(),
        "prices": {"prompt_per_1m": price_in, "completion_per_1m": price_out},
        "manifest": manifest_info,
        "training_inventory": inventory_info,
        "provenance": {
            "git_sha": git_sha,
            "git_dirty": git_dirty,
            "policy_path": str(policy_path) if policy_path else None,
            "policy_classes": {
                "hive-rule": type(rule_policy).__name__ if rule_policy else None,
                "hive-trained": (type(trained_policy).__name__
                                 if trained_policy else None),
            },
            "unsigned_model_override":
                os.environ.get("HIVE_ALLOW_UNSIGNED_MODEL") == "1",
            "temperature": temperature,
            "max_turns": max_turns,
            "max_tokens_per_call": max_tokens,
        },
        "summaries": {
            c: summarize_condition(rows, c, price_in=price_in,
                                   price_out=price_out)
            for c in conditions
        },
        "paired": paired_grid(
            rows, conditions,
            expected={(t.id, p) for t in tasks for p in range(repeat)}),
        "results": rows,
    }
    write_report_atomic(out_dir / "report.json", report)
    _log.info("wrote %s (status=%s)", out_dir / "report.json", status)
    return report


# ---------------------------------------------------------------------------
# Validation shared by dry-run and run
# ---------------------------------------------------------------------------


def validate_setup(*, manifest_path: Path, inventory_path: Path,
                   conditions: list[str], policy_path: Path | None,
                   check_policy: bool) -> tuple[list[Task], dict, dict]:
    """Everything that can be checked without spending a token."""
    _check_conditions(conditions)
    tasks, manifest_info = load_manifest(manifest_path)
    verify_source_hashes(tasks, manifest_info)
    inventory = load_training_inventory(inventory_path)
    check_overlap(manifest_info, inventory)
    inventory_info = {
        "path": str(inventory_path),
        "sha256": _sha256_file(inventory_path),
        "task_ids": len(inventory["task_ids"]),
        "source_hashes": len(inventory["source_hashes"]),
    }
    if "hive-trained" in conditions:
        if policy_path is None:
            raise ExternalBenchError(
                "condition 'hive-trained' requires --policy-path")
        if not Path(policy_path).is_file():
            raise ExternalBenchError(f"--policy-path not found: {policy_path}")
        if check_policy:
            policy = load_trained_policy(policy_path)  # signature guard runs
            _log.info("trained policy verified: %s (%s)",
                      policy_path, type(policy).__name__)
    return tasks, manifest_info, inventory_info


def dry_run(*, manifest_path: Path, inventory_path: Path,
            conditions: list[str], repeat: int,
            policy_path: Path | None) -> int:
    """Validate the whole setup without touching a backend."""
    tasks, manifest_info, inventory_info = validate_setup(
        manifest_path=manifest_path, inventory_path=inventory_path,
        conditions=conditions, policy_path=policy_path, check_policy=True)
    print(f"manifest: {manifest_info['path']} "
          f"(sha256 {manifest_info['sha256'][:16]}…)")
    print(f"tasks: {len(tasks)} — source hashes verified")
    print(f"training inventory: {inventory_info['task_ids']} ids, "
          f"{inventory_info['source_hashes']} hashes — no overlap")
    print(f"episodes planned: {len(tasks) * repeat * len(conditions)} "
          f"({len(tasks)} tasks x {repeat} pass(es) x {len(conditions)} conditions)")
    for i, task in enumerate(tasks):
        print(f"  {task.id}: order {' -> '.join(condition_order(i, conditions))}")
    print("dry-run OK — no model calls made")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _positive_int(value: str) -> int:
    iv = int(value)
    if iv <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return iv


def _positive_float(value: str) -> float:
    fv = float(value)
    if fv <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return fv


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    hp = sub.add_parser("hash-tasks",
                        help="print source_sha256 entries for a tasks directory")
    hp.add_argument("--tasks-dir", required=True,
                    help="directory containing one subdirectory per task")

    for name in ("dry-run", "run"):
        sp = sub.add_parser(name)
        sp.add_argument("--manifest", required=True,
                        help="versioned external manifest (schema_version 1)")
        sp.add_argument("--training-inventory", required=True,
                        help="JSON {task_ids, source_hashes} the eval must not overlap")
        sp.add_argument("--conditions", nargs="*", choices=CONDITIONS,
                        default=list(CONDITIONS))
        sp.add_argument("--repeat", type=_positive_int, default=1)
        sp.add_argument("--policy-path", default=None,
                        help="signed joblib for the hive-trained condition "
                             "(loaded via CPURouterPolicy.load — unsigned "
                             "artifacts are refused)")
    sub.choices["run"].add_argument(
        "--output", required=True,
        help="new output directory (must not already exist)")
    run_p = sub.choices["run"]
    run_p.add_argument("--model", required=True)
    run_p.add_argument("--endpoint", default=None,
                       help="OpenAI-compatible base URL; default: the env var "
                            "named by --endpoint-env")
    run_p.add_argument("--endpoint-env", default="OPENAI_BASE_URL")
    run_p.add_argument("--api-key-env", default="OPENAI_API_KEY",
                       help="env var holding the API key (never a CLI value)")
    run_p.add_argument("--max-requests", type=_positive_int, required=True)
    run_p.add_argument("--max-tokens-total", type=_positive_int, required=True)
    run_p.add_argument("--wall-seconds", type=_positive_float, required=True)
    run_p.add_argument("--max-turns", type=_positive_int, default=25)
    run_p.add_argument("--max-tokens", type=_positive_int, default=4096,
                       help="per-call completion cap passed to the backend")
    run_p.add_argument("--temperature", type=float, default=0.0)
    run_p.add_argument("--price-in", type=float, default=0.22,
                       help="USD per 1M prompt tokens for the cost estimate")
    run_p.add_argument("--price-out", type=float, default=0.66,
                       help="USD per 1M completion tokens")

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        if args.cmd == "hash-tasks":
            tasks_dir = Path(args.tasks_dir)
            if not tasks_dir.is_dir():
                raise ExternalBenchError(f"not a directory: {tasks_dir}")
            out = {d.name: hash_task_source(d)
                   for d in sorted(tasks_dir.iterdir()) if d.is_dir()}
            print(json.dumps(out, indent=2))
            return 0

        if args.cmd == "dry-run":
            return dry_run(manifest_path=Path(args.manifest),
                           inventory_path=Path(args.training_inventory),
                           conditions=list(args.conditions),
                           repeat=args.repeat,
                           policy_path=Path(args.policy_path)
                           if args.policy_path else None)

        # run
        out_dir = Path(args.output)
        if out_dir.exists():
            raise ExternalBenchError(
                f"--output already exists: {out_dir} — a run always writes a "
                "fresh directory so partial runs are never overwritten")
        endpoint = args.endpoint or os.environ.get(args.endpoint_env, "")
        endpoint = endpoint.removesuffix("/v1") or None
        if endpoint is None:
            raise ExternalBenchError(
                f"no endpoint: pass --endpoint or set {args.endpoint_env}")
        api_key = os.environ.get(args.api_key_env)
        if api_key is None:
            _log.warning("%s is not set — calling the endpoint without a key",
                         args.api_key_env)

        tasks, manifest_info, inventory_info = validate_setup(
            manifest_path=Path(args.manifest),
            inventory_path=Path(args.training_inventory),
            conditions=list(args.conditions),
            policy_path=Path(args.policy_path) if args.policy_path else None,
            check_policy=True)

        from hive.llm import make_backend

        backend = make_backend("openai", endpoint=endpoint, model=args.model,
                               api_key=api_key)
        report = run_suite(
            tasks=tasks, manifest_info=manifest_info,
            inventory_info=inventory_info, backend=backend,
            conditions=list(args.conditions), repeat=args.repeat,
            policy_path=Path(args.policy_path) if args.policy_path else None,
            budgets={"max_requests": args.max_requests,
                     "max_tokens_total": args.max_tokens_total,
                     "wall_seconds": args.wall_seconds},
            out_dir=out_dir, max_turns=args.max_turns,
            max_tokens=args.max_tokens, temperature=args.temperature,
            price_in=args.price_in, price_out=args.price_out,
            model=args.model, endpoint=endpoint)
        return {"complete": 0, "partial": 4, "aborted": 3}[report["status"]]
    except ExternalBenchError as exc:
        _log.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
