"""CPU routing accuracy eval through HiveStack + busybee-cpu.

Measures the third pillar of Hive evidence: does the CPU policy pick the
right tool often enough to skip an LLM call?

Pipeline:
1. Train ``CpuActionPolicy`` on held-out training JSONL (busyBee-cpu format)
2. Wire it into ``HiveStack(busybee_policy=...)``
3. For each eval row, call ``stack.route(row)`` and grade against
   ``target_action`` using busyBee's own metrics (action accuracy, semantic
   args, escalation rate, latency)

Baselines:
- **always_escalate**: every turn goes to the LLM (0% CPU routing)
- **majority_class**: always predict the most common tool in training

Usage::

    pip install busybee-cpu   # or: pip install -e ../busyBee-cpu
    python3 scripts/routing_eval.py
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import platform
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "routing"


def _require_busybee():
    try:
        from busybee_cpu.policy import CpuActionPolicy  # noqa: F401
        from busybee_cpu.io import load_jsonl  # noqa: F401
        from busybee_cpu.metrics import (  # noqa: F401
            args_semantic,
            correct_action,
            schema_valid,
            valid_action,
        )
    except ImportError as exc:
        raise SystemExit(
            "busybee_cpu not installed. Clone https://github.com/DJLougen/busyBee-cpu "
            "and run: pip install -e ../busyBee-cpu"
        ) from exc


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        ).stdout.strip()
    except Exception:
        return "unknown"


def _default_paths() -> tuple[Path, Path]:
    """Prefer bundled fixtures; fall back to a sibling busyBee-cpu checkout."""
    train = FIXTURE_DIR / "train_synthetic_200.jsonl"
    eval_ = FIXTURE_DIR / "eval_synthetic_50.jsonl"
    sibling = REPO_ROOT.parent / "busyBee-cpu" / "examples"
    if sibling.is_dir():
        full_train = sibling / "train_synthetic.jsonl"
        full_eval = sibling / "eval_synthetic.jsonl"
        if full_train.exists() and full_eval.exists():
            return full_train, full_eval
    return train, eval_


def _evaluate_rows(
    rows: list[dict[str, Any]],
    predict_fn,
    *,
    label: str,
) -> dict[str, Any]:
    from busybee_cpu.metrics import args_semantic, correct_action, schema_valid, valid_action

    by_tool: dict[str, dict[str, int]] = {}
    latencies_ms: list[float] = []
    counts = {
        "n": 0,
        "valid_action": 0,
        "schema_valid": 0,
        "correct_action": 0,
        "args_semantic": 0,
        "escalated": 0,
    }

    for row in rows:
        target = row["target_action"]
        tool = str(target.get("tool") or "")
        bucket = by_tool.setdefault(tool, {"n": 0, "correct": 0})
        bucket["n"] += 1
        counts["n"] += 1

        t0 = time.perf_counter()
        pred = predict_fn(row)
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)

        if valid_action(pred, row):
            counts["valid_action"] += 1
        if schema_valid(pred, row):
            counts["schema_valid"] += 1
        if correct_action(pred, target):
            counts["correct_action"] += 1
            bucket["correct"] += 1
        if args_semantic(pred, target):
            counts["args_semantic"] += 1
        if str(pred.get("tool") or pred.get("action") or "") == "escalate":
            counts["escalated"] += 1

    n = max(1, counts["n"])
    latencies_ms.sort()
    return {
        "label": label,
        "rows": counts["n"],
        "action_accuracy_pct": 100.0 * counts["correct_action"] / n,
        "args_semantic_pct": 100.0 * counts["args_semantic"] / n,
        "escalation_rate_pct": 100.0 * counts["escalated"] / n,
        "valid_action_pct": 100.0 * counts["valid_action"] / n,
        "schema_valid_pct": 100.0 * counts["schema_valid"] / n,
        "latency_p50_ms": latencies_ms[len(latencies_ms) // 2],
        "latency_p95_ms": latencies_ms[int(len(latencies_ms) * 0.95)],
        "throughput_routes_per_s": n / (sum(latencies_ms) / 1000.0),
        "by_tool": {
            tool: {
                "n": b["n"],
                "accuracy_pct": 100.0 * b["correct"] / b["n"] if b["n"] else 0.0,
            }
            for tool, b in sorted(by_tool.items())
        },
    }


def run_routing_eval(
    train_path: Path,
    eval_path: Path,
    *,
    augment: bool = True,
) -> dict[str, Any]:
    _require_busybee()
    from busybee_cpu.io import load_jsonl
    from busybee_cpu.policy import CpuActionPolicy
    from hive.stack import HiveStack

    train_rows = load_jsonl(train_path)
    eval_rows = load_jsonl(eval_path)

    t0 = time.perf_counter()
    policy = CpuActionPolicy.train(train_rows, augment=augment)
    train_s = time.perf_counter() - t0

    stack = HiveStack(busybee_policy=policy)

    def hive_predict(row: dict[str, Any]) -> dict[str, Any]:
        decision = stack.route(row)
        return {
            "tool": decision.tool,
            "args": decision.args,
            "confidence": decision.confidence,
            "escalated": decision.escalated,
        }

    def policy_predict(row: dict[str, Any]) -> dict[str, Any]:
        return policy.predict(row)

    hive_result = _evaluate_rows(eval_rows, hive_predict, label="hive_stack")
    direct_result = _evaluate_rows(eval_rows, policy_predict, label="busybee_direct")

    # Baselines
    majority = Counter(
        str(r["target_action"].get("tool") or "") for r in train_rows
    ).most_common(1)[0][0]

    def always_escalate(_row: dict[str, Any]) -> dict[str, Any]:
        return {"tool": "escalate", "args": {"reason": "baseline"}}

    def majority_class(_row: dict[str, Any]) -> dict[str, Any]:
        return {"tool": majority, "args": {}}

    baselines = {
        "always_escalate": _evaluate_rows(eval_rows, always_escalate, label="always_escalate"),
        "majority_class": _evaluate_rows(eval_rows, majority_class, label=f"majority_{majority}"),
    }

    return {
        "train_path": str(train_path),
        "eval_path": str(eval_path),
        "train_rows": len(train_rows),
        "eval_rows": len(eval_rows),
        "train_seconds": round(train_s, 3),
        "augment": augment,
        "hive_stack": hive_result,
        "busybee_direct": direct_result,
        "baselines": baselines,
        "hive_matches_direct": (
            hive_result["action_accuracy_pct"] == direct_result["action_accuracy_pct"]
        ),
    }


def render_report(result: dict[str, Any]) -> str:
    h = result["hive_stack"]
    b_esc = result["baselines"]["always_escalate"]
    b_maj = result["baselines"]["majority_class"]
    lines = [
        "# CPU Routing Accuracy Eval",
        "",
        "Does the CPU policy pick the right tool often enough to skip an LLM call?",
        "Evaluated **through HiveStack.route()** so this measures the full Hive",
        "integration path, not just the raw classifier.",
        "",
        f"- Train: `{result['train_path']}` ({result['train_rows']} rows, "
        f"{result['train_seconds']}s, augment={result['augment']})",
        f"- Eval: `{result['eval_path']}` ({result['eval_rows']} held-out rows)",
        f"- Machine: {result['machine']}",
        f"- Commit: `{result['commit']}` — {result['timestamp']}",
        "",
        "## Overall",
        "",
        "| System | Action accuracy | Args semantic | Escalation rate | P50 latency |",
        "|---|---|---|---|---|",
        f"| HiveStack + busybee | **{h['action_accuracy_pct']:.1f}%** | "
        f"{h['args_semantic_pct']:.1f}% | {h['escalation_rate_pct']:.1f}% | "
        f"{h['latency_p50_ms']:.2f} ms |",
        f"| busybee direct | {result['busybee_direct']['action_accuracy_pct']:.1f}% | "
        f"{result['busybee_direct']['args_semantic_pct']:.1f}% | "
        f"{result['busybee_direct']['escalation_rate_pct']:.1f}% | "
        f"{result['busybee_direct']['latency_p50_ms']:.2f} ms |",
        f"| always escalate (baseline) | {b_esc['action_accuracy_pct']:.1f}% | — | 100% | — |",
        f"| majority class ({b_maj['label']}) | {b_maj['action_accuracy_pct']:.1f}% | — | "
        f"{b_maj['escalation_rate_pct']:.1f}% | — |",
        "",
        f"Throughput: {h['throughput_routes_per_s']:,.0f} routes/s via HiveStack.",
        f"HiveStack matches direct busybee: {result['hive_matches_direct']}.",
        "",
        "## Per tool (HiveStack)",
        "",
        "| Tool | Eval rows | Accuracy |",
        "|---|---|---|",
    ]
    for tool, stats in h["by_tool"].items():
        lines.append(f"| {tool} | {stats['n']} | {stats['accuracy_pct']:.1f}% |")
    lines += [
        "",
        "## How to read this",
        "",
        "- **Action accuracy** is the routing metric: did the CPU pick the same",
        "  tool a human/agent would? Wrong picks waste one turn; the loop retries.",
        "- **Args semantic** is harder — filenames and patch bodies need not be",
        "  perfect at routing time; the resolver fills them from state on the",
        "  next turn.",
        "- This eval uses busyBee's *synthetic held-out* set (200 rows in the",
        "  full corpus; bundled 50-row sample for CI). For SWE-bench held-out",
        "  numbers see busyBee-cpu's `reports/honest_evaluation.md` (96.4% on",
        "  11,881 unseen issues with the combined model).",
        "",
        "## Reproduce",
        "",
        "```bash",
        "pip install -e ../busyBee-cpu   # or pip install busybee-cpu",
        "python3 scripts/routing_eval.py",
        "```",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description=__doc__)
    default_train, default_eval = _default_paths()
    parser.add_argument("--train", type=Path, default=default_train)
    parser.add_argument("--eval", type=Path, default=default_eval)
    parser.add_argument("--no-augment", action="store_true")
    parser.add_argument(
        "--json", type=Path, default=REPO_ROOT / "results" / "routing_eval.json"
    )
    parser.add_argument(
        "--report", type=Path, default=REPO_ROOT / "docs" / "benchmarks" / "routing.md"
    )
    args = parser.parse_args(argv)

    result = run_routing_eval(args.train, args.eval, augment=not args.no_augment)
    result.update(
        machine=f"{platform.machine()} / {platform.system()} / {os.cpu_count()} cores",
        commit=_git_commit(),
        timestamp=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    )

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(result, indent=2) + "\n")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_report(result))

    h = result["hive_stack"]
    print(f"train:             {result['train_rows']} rows in {result['train_seconds']}s")
    print(f"eval:              {result['eval_rows']} rows")
    print(f"action accuracy:   {h['action_accuracy_pct']:.1f}% (HiveStack)")
    print(f"args semantic:     {h['args_semantic_pct']:.1f}%")
    print(f"escalation rate:   {h['escalation_rate_pct']:.1f}%")
    print(f"throughput:        {h['throughput_routes_per_s']:,.0f} routes/s")
    print(f"json:              {args.json}")
    print(f"report:            {args.report}")
    return result


if __name__ == "__main__":
    main()
