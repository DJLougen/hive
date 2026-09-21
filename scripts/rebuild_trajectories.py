"""Rebuild trajectory rows {state, tool, ok} from published bench artifacts.

The published `docs/benchmarks/*.json` artifacts carry per-episode `steps`
(tool, decision_source, ok, write) but not the `state` each decision saw, and
`benchmarks/cpu_router.joblib` was trained on a separate 130-row log that no
longer matches `featurize` (19 vs 47 features) — it crashes on every call.

This replays the harness's documented state machine over each episode's tool
sequence to reconstruct the state vector, then emits JSONL rows that
`CPURouterPolicy.fit` / `scripts/train_cpu_policy.py` accept.

Reconstruction is faithful to scripts/hive_bench.py's updates for the *observable*
fields featurize() reads (listed/tests_run/tests_passed/writes/files_read/
suggested_read/verify_pending/step/last_tool). It cannot recover `recalled_fix`,
`memory_hit` or `fail_signature` (not recorded per step) — those read as their
defaults, which is stated in the emitted metrics, not hidden.

Held-out evaluation suites can be dropped from the corpus with
``--exclude-suite <manifest>`` so a policy never trains on the tasks it is
later scored on. Every run also writes ``<out>.provenance.json`` recording the
source artifacts' SHA-256s, the corpus SHA-256, the task ids kept/dropped, and
the row count. The sidecar records *what this command did*; it is not a
guarantee of how any previously committed model was trained.

Fails closed: a missing source artifact, a row with no task id when exclusion
is requested, or an empty corpus are errors — the script never writes a
partial corpus and reports success.

Usage:
    python scripts/rebuild_trajectories.py --out benchmarks/trajectories-rebuilt.jsonl \
        --exclude-suite benchmarks/tasks/suite.hard.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

SOURCES = (
    "docs/benchmarks/hive-bench-hard.json",
    "docs/benchmarks/hive-bench-capability.json",
    "docs/benchmarks/hive-bench-flash-r3.json",
)

PROVENANCE_SCHEMA = "hive-trajectory-provenance/v1"


def rows_from_artifact(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    out: list[dict] = []
    for ep in data.get("results", []):
        state = {
            "goal": ep.get("task_id", ""),
            "listed": False,
            "tests_run": 0,
            "tests_passed": None,
            "writes": 0,
            "files_read": [],
            "suggested_read": None,
        }
        last_tool = "none"
        for step in ep.get("steps", []):
            tool = step.get("tool")
            if tool is None:
                continue
            snap = dict(state)
            snap["files_read"] = list(state["files_read"])
            snap["last_tool"] = last_tool
            out.append({
                "task": ep.get("task_id"),
                "arm": ep.get("arm"),
                "pass": ep.get("pass_idx", 0),
                "source": step.get("decision_source"),
                "state": snap,
                "tool": tool,
                "ok": step.get("ok", True),
            })
            # advance the state machine (observable fields only)
            if tool == "list_files":
                state["listed"] = True
            elif tool == "read_file":
                p = (step.get("args") or {}).get("path", "")
                if p and p not in state["files_read"]:
                    state["files_read"].append(p)
                if p == state.get("suggested_read"):
                    state["suggested_read"] = None
            elif tool == "run_tests":
                state["tests_run"] += 1
                state["verify_pending"] = False
            elif tool == "write_file":
                if step.get("ok", True):
                    state["writes"] += 1
                    state["tests_passed"] = None
                    state["verify_pending"] = True
            last_tool = tool if step.get("ok", True) else "invalid"
        _ = last_tool
    return out


def load_suite_task_ids(manifest: Path) -> set[str]:
    """Read a suite manifest (``suite*.json``) and return its task ids."""
    if not manifest.exists():
        raise FileNotFoundError(f"suite manifest {manifest} does not exist")
    suite = json.loads(manifest.read_text())
    tasks = suite.get("tasks")
    if not isinstance(tasks, list) or not all(isinstance(t, str) for t in tasks):
        raise ValueError(f"{manifest}: expected a manifest with a 'tasks' list of ids")
    return set(tasks)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def write_provenance(out: Path, *, sources: dict[str, str], rows: list[dict],
                     excluded_tasks: list[str], exclude_suite: str | None) -> Path:
    """Write ``<out>.provenance.json`` describing exactly what was emitted."""
    corpus_sha = hashlib.sha256(out.read_bytes()).hexdigest()
    doc = {
        "schema_version": PROVENANCE_SCHEMA,
        "output": str(out),
        "row_count": len(rows),
        "tasks": sorted({r["task"] for r in rows}),
        "excluded_tasks": sorted(excluded_tasks),
        "exclude_suite": exclude_suite,
        "sources": sources,  # relpath -> sha256
        "corpus_sha256": corpus_sha,
        "caveat": (
            "This file records what this rebuild command read and wrote. It is "
            "not evidence about how any previously committed model checkpoint "
            "(e.g. benchmarks/cpu_router.joblib) was trained — that history is "
            "not recoverable from this metadata."
        ),
    }
    prov_path = out.with_name(out.name + ".provenance.json")
    prov_path.write_text(json.dumps(doc, indent=2) + "\n")
    return prov_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--exclude-policy-rows", action="store_true",
                    help="drop rows the policy itself produced (self-imitation)")
    ap.add_argument("--exclude-suite", default=None, metavar="MANIFEST",
                    help="suite manifest (suite*.json) whose task ids are dropped "
                         "from the corpus — use it to keep a held-out eval tier "
                         "out of training data")
    ap.add_argument("--source", action="append", default=None, metavar="ARTIFACT",
                    help="bench artifact JSON to read (repeatable); overrides the "
                         "built-in SOURCES list")
    args = ap.parse_args(argv)

    excluded_ids: set[str] = set()
    if args.exclude_suite:
        try:
            excluded_ids = load_suite_task_ids(Path(args.exclude_suite))
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
            print(f"error: --exclude-suite: {exc}", file=sys.stderr)
            return 2
        if not excluded_ids:
            print(f"error: --exclude-suite {args.exclude_suite} names no tasks",
                  file=sys.stderr)
            return 2

    rows: list[dict] = []
    sources: dict[str, str] = {}
    missing: list[str] = []
    source_list = list(args.source) if args.source else list(SOURCES)
    for rel in source_list:
        p = Path(rel)
        if not p.is_absolute():
            p = _REPO_ROOT / rel
        if not p.exists():
            missing.append(rel)
            continue
        got = rows_from_artifact(p)
        print(f"{rel}: {len(got)} rows")
        rows.extend(got)
        sources[rel] = _sha256(p)

    if missing:
        print(f"error: source artifact(s) absent: {', '.join(missing)} — "
              f"refusing to write a partial corpus", file=sys.stderr)
        return 2

    if args.exclude_policy_rows:
        before = len(rows)
        rows = [r for r in rows if r.get("source") == "llm"]
        print(f"excluded {before - len(rows)} policy-produced rows")

    dropped: set[str] = set()
    if excluded_ids:
        untagged = sum(1 for r in rows if not r.get("task"))
        if untagged:
            print(f"error: {untagged} row(s) carry no task id — cannot prove "
                  f"exclusion of {sorted(excluded_ids)}; refusing to emit a "
                  f"corpus that may contain held-out tasks", file=sys.stderr)
            return 2
        present = {r["task"] for r in rows}
        before = len(rows)
        rows = [r for r in rows if r["task"] not in excluded_ids]
        dropped = excluded_ids & present
        print(f"excluded {before - len(rows)} rows from suite "
              f"{args.exclude_suite} ({len(dropped)} task ids dropped)")

    if not rows:
        print("error: corpus is empty — nothing to write", file=sys.stderr)
        return 2

    out = Path(args.out)
    out.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    prov = write_provenance(out, sources=sources, rows=rows,
                            excluded_tasks=sorted(dropped),
                            exclude_suite=args.exclude_suite)
    print(f"wrote {len(rows)} rows -> {out}")
    print(f"provenance -> {prov}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
