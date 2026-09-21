"""Bounded development A/B of legacy versus static re-export navigation.

Reads a predeclared JSON plan; preserves every completed episode and API response.
This is a development experiment, not a held-out generalization evaluation.
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hive import HiveStack
from hive.cpu_policy import CPURouterPolicy
from hive.llm import make_backend
from hive.rule_fast import RuleFastHoneyComb
from hive.rust_brain import RustBrain
from scripts.hive_bench import load_tasks, run_episode


class BudgetStop(BaseException):
    """Not retried by the benchmark's ordinary Exception retry handler."""


class MeteredBackend:
    def __init__(self, backend: Any, plan: dict[str, Any], ledger: Path) -> None:
        self.backend, self.plan, self.ledger = backend, plan, ledger
        self.started = time.monotonic()
        self.requests = self.prompt_tokens = self.completion_tokens = 0
        self.context: dict[str, Any] = {}

    def chat(self, messages: Any, **kwargs: Any) -> Any:
        limits = self.plan["limits"]
        # Prices are estimates, not an invoice; independent request/time caps apply.
        estimated_usd = (self.prompt_tokens * 0.22 + self.completion_tokens * 0.66) / 1_000_000
        # Conservatively reserve one token per serialized input byte plus the
        # maximum output; do not start a call that could exhaust the estimate.
        input_bytes = len(json.dumps({"messages": messages, **kwargs}).encode("utf-8")) + 512
        reserve_usd = (input_bytes * 0.22 + kwargs.get("max_tokens", 4096) * 0.66) / 1_000_000
        if (self.requests >= limits["max_llm_requests"]
                or estimated_usd + reserve_usd >= limits["estimated_usd_limit"]
                or time.monotonic() - self.started >= limits["wall_seconds"]):
            raise BudgetStop("predeclared request/cost/time limit reached")
        self.requests += 1
        try:
            response = self.backend.chat(messages, **kwargs)
        except Exception as exc:
            # An unknown-cost failed request aborts this comparison, not a free retry.
            raise BudgetStop(f"request failed ({type(exc).__name__}); usage unknown") from exc
        row = {"request": self.requests, **self.context, "response": dataclasses.asdict(response)}
        with self.ledger.open("a", encoding="utf-8") as out:
            out.write(json.dumps(row) + "\n")
        if response.prompt_tokens <= 0 or response.completion_tokens <= 0:
            raise BudgetStop("response usage absent or nonpositive; comparison invalid")
        self.prompt_tokens += response.prompt_tokens
        self.completion_tokens += response.completion_tokens
        return response


def summarize(rows: list[dict[str, Any]], expected: int) -> dict[str, Any]:
    summaries = {}
    for condition in ("legacy", "reexports"):
        arm = [r for r in rows if r["condition"] == condition]
        summaries[condition] = {
            "episodes": len(arm),
            "resolved": sum(r["resolved"] for r in arm),
            "llm_calls": sum(r["llm_calls"] for r in arm),
            "prompt_tokens": sum(r["prompt_tokens"] for r in arm),
            "completion_tokens": sum(r["completion_tokens"] for r in arm),
            "wall_clock_s": sum(r["wall_clock_s"] for r in arm),
            "per_task_resolved": {
                task: sum(r["resolved"] for r in arm if r["task_id"] == task)
                for task in sorted({r["task_id"] for r in arm})
            },
        }
    a, b = summaries["legacy"], summaries["reexports"]
    identities = [(r["condition"], r["task_id"], r["pass_idx"]) for r in rows]
    paired_keys = {
        condition: {(r["task_id"], r["pass_idx"]) for r in rows if r["condition"] == condition}
        for condition in ("legacy", "reexports")
    }
    complete = (expected > 0 and len(rows) == 2 * expected
                and a["episodes"] == b["episodes"] == expected
                and paired_keys["legacy"] == paired_keys["reexports"]
                and len(set(identities)) == len(identities)
                and all(r["usage_recorded"] and not r["crashed"] for r in rows))
    same_keys = a["per_task_resolved"].keys() == b["per_task_resolved"].keys()
    keep = (complete and same_keys and b["llm_calls"] < a["llm_calls"]
            and all(b["per_task_resolved"][t] >= n for t, n in a["per_task_resolved"].items()))
    return {"conditions": summaries, "complete": complete, "observed_keep_gate": keep,
            "caveat": "Observed counts only; not a statistical proof of noninferiority."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    if plan["status"] != "planned" or plan["results"]:
        parser.error("plan is not fresh; refusing to overwrite an existing run")
    directory = args.plan.resolve().parent
    ledger = directory / "responses.jsonl"
    if ledger.exists():
        parser.error("response ledger already exists")
    model_path = ROOT / "benchmarks/cpu_router.joblib"
    policy = CPURouterPolicy.load(model_path)
    tasks = load_tasks(ROOT / "benchmarks/tasks", plan["tasks"])
    if len(tasks) != len(plan["tasks"]):
        parser.error("task selection incomplete")
    plan["execution"] = {
        "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "git_diff_sha256": hashlib.sha256(subprocess.check_output(["git", "diff"], cwd=ROOT)).hexdigest(),
        "policy_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
        "source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in (ROOT / "hive/source_navigation.py", ROOT / "scripts/hive_bench.py",
                                    Path(__file__).resolve())},
        "endpoint": "https://api.fireworks.ai/inference",
        "estimated_prices_per_million": {"input": 0.22, "output": 0.66},
    }
    backend = MeteredBackend(make_backend(
        "openai", endpoint=plan["execution"]["endpoint"], model=plan["model"],
        api_key=os.environ["FIREWORKS_API_KEY"]), plan, ledger)
    scratch = Path(tempfile.mkdtemp(prefix="hive-navigation-"))
    plan["execution"]["scratch"] = str(scratch)
    plan["status"] = "running"
    args.plan.write_text(json.dumps(plan, indent=2) + "\n")
    print("comparison started", flush=True)
    try:
        for rep in range(plan["repeats"]):
            for index, task in enumerate(tasks):
                order = ("legacy", "reexports") if (rep + index) % 2 == 0 else ("reexports", "legacy")
                for condition in order:
                    backend.context = {"condition": condition, "task_id": task.id, "pass_idx": rep}
                    stack = HiveStack(busybee_policy=copy.deepcopy(policy),
                                      honey_comb=RuleFastHoneyComb(), rust_brain=RustBrain())
                    result = run_episode(
                        task, arm="hive", backend=backend, stack=stack,
                        max_turns=plan["max_turns"], max_tokens=plan["max_tokens"],
                        temperature=plan["temperature"], pass_idx=rep,
                        source_navigation=condition,
                        workdir=scratch / f"{condition}-p{rep}-{task.id}")
                    plan["results"].append({"condition": condition, **dataclasses.asdict(result)})
                    plan["summary"] = summarize(plan["results"], len(tasks) * plan["repeats"])
                    args.plan.write_text(json.dumps(plan, indent=2) + "\n")
                    print(f"{len(plan['results'])}: {condition} {task.id} p{rep} "
                          f"resolved={result.resolved} calls={result.llm_calls}", flush=True)
        plan["status"] = "completed"
    except (BudgetStop, Exception) as exc:
        plan["status"] = "aborted"
        plan["abort_reason"] = f"{type(exc).__name__}: {exc}"
    finally:
        plan["requests"] = backend.requests
        plan["summary"] = summarize(plan["results"], len(tasks) * plan["repeats"])
        args.plan.write_text(json.dumps(plan, indent=2) + "\n")
    print(json.dumps(plan["summary"], indent=2), flush=True)
    return 0 if plan["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
