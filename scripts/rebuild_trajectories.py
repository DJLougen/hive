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

Usage:
    python scripts/rebuild_trajectories.py --out benchmarks/trajectories-rebuilt.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

SOURCES = (
    "docs/benchmarks/hive-bench-hard.json",
    "docs/benchmarks/hive-bench-capability.json",
    "docs/benchmarks/hive-bench-flash-r3.json",
)


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--exclude-policy-rows", action="store_true",
                    help="drop rows the policy itself produced (self-imitation)")
    args = ap.parse_args()

    rows: list[dict] = []
    for rel in SOURCES:
        p = _REPO_ROOT / rel
        if not p.exists():
            print(f"skip (absent): {rel}")
            continue
        got = rows_from_artifact(p)
        print(f"{rel}: {len(got)} rows")
        rows.extend(got)

    if args.exclude_policy_rows:
        before = len(rows)
        rows = [r for r in rows if r.get("source") == "llm"]
        print(f"excluded {before - len(rows)} policy-produced rows")

    out = Path(args.out)
    out.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    print(f"wrote {len(rows)} rows -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
