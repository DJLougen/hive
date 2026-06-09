"""Multi-step agent-loop eval: can the model navigate a debug trajectory?

At each step of a fixed debugging episode (run tests → read file → patch →
re-test), the model sees the transcript so far and must pick the next tool.
Compare **raw** vs **Hive-compressed** transcripts.

This is the closest CPU-only proxy for SWE-bench-style task success we can
run without a frontier model: multi-step, tool-selection, real compression
in the loop.

Usage::

    pip install torch transformers
    python3 scripts/agent_loop_eval.py
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_loop_corpus import EPISODES, Episode  # noqa: E402
from llm_fidelity_eval import LocalBackend, make_backend  # noqa: E402

from hive.stack import HiveStack  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

SYSTEM_PROMPT = (
    "You are a software agent choosing the next tool to call. "
    "Reply with ONLY one tool name from this list: read_file, run_tests, "
    "apply_patch, escalate. No explanation."
)

TOOLS = {"read_file", "run_tests", "apply_patch", "escalate"}


def _parse_tool(answer: str) -> str | None:
    text = answer.strip().lower()
    for tool in TOOLS:
        if re.search(rf"\b{re.escape(tool)}\b", text):
            return tool
    return None


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


def _build_user_prompt(episode: Episode, transcript: list[tuple[str, str]]) -> str:
    parts = [f"Goal: {episode.goal}", "", "Transcript so far:"]
    for role, content in transcript:
        parts.append(f"[{role}]\n{content}")
    parts.append("")
    parts.append("What tool do you call next? Reply with ONLY the tool name.")
    return "\n".join(parts)


def run_episode(
    episode: Episode,
    *,
    condition: str,
    stack: HiveStack,
    backend: Any,
    max_new_tokens: int,
) -> dict[str, Any]:
    transcript: list[tuple[str, str]] = []
    step_rows: list[dict[str, Any]] = []
    total_tokens = 0

    for i, step in enumerate(episode.steps):
        user = _build_user_prompt(episode, transcript)
        t0 = time.perf_counter()
        answer, prompt_tokens = backend.ask(SYSTEM_PROMPT, user, max_new_tokens)
        latency_s = time.perf_counter() - t0
        total_tokens += prompt_tokens
        predicted = _parse_tool(answer)
        correct = predicted == step.expected_tool
        step_rows.append(
            {
                "step": i,
                "expected_tool": step.expected_tool,
                "predicted_tool": predicted,
                "correct": correct,
                "answer": answer.strip(),
                "prompt_tokens": prompt_tokens,
                "latency_s": round(latency_s, 2),
            }
        )
        # Append this step's tool output to transcript for the next turn.
        content = step.tool_output
        if condition == "compressed":
            content = stack.compress("tool", content).content
        transcript.append(("tool", content))

    correct_steps = sum(1 for r in step_rows if r["correct"])
    return {
        "episode_id": episode.id,
        "condition": condition,
        "steps": len(episode.steps),
        "correct_steps": correct_steps,
        "step_accuracy_pct": 100.0 * correct_steps / len(episode.steps),
        "episode_resolved": correct_steps == len(episode.steps),
        "total_prompt_tokens": total_tokens,
        "step_rows": step_rows,
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def _agg(subset: list[dict[str, Any]]) -> dict[str, Any]:
        steps = sum(r["steps"] for r in subset)
        correct = sum(r["correct_steps"] for r in subset)
        resolved = sum(1 for r in subset if r["episode_resolved"])
        return {
            "episodes": len(subset),
            "total_steps": steps,
            "correct_steps": correct,
            "step_accuracy_pct": 100.0 * correct / steps if steps else 0.0,
            "resolve_rate_pct": 100.0 * resolved / len(subset) if subset else 0.0,
            "avg_prompt_tokens_per_episode": (
                sum(r["total_prompt_tokens"] for r in subset) / len(subset)
                if subset
                else 0.0
            ),
        }

    return {
        "raw": _agg([r for r in rows if r["condition"] == "raw"]),
        "compressed": _agg([r for r in rows if r["condition"] == "compressed"]),
    }


def render_report(result: dict[str, Any]) -> str:
    raw = result["aggregate"]["raw"]
    comp = result["aggregate"]["compressed"]
    saved = 100.0 * (1 - comp["avg_prompt_tokens_per_episode"] / raw["avg_prompt_tokens_per_episode"])
    lines = [
        "# Multi-Step Agent Loop Eval",
        "",
        "Can a model navigate a fixed debugging trajectory (test → read →",
        "patch → re-test) when tool outputs are **raw** vs **Hive-compressed**?",
        "",
        f"- Model: `{result['backend']}`",
        f"- Episodes: {result['episodes']} ({result['total_steps']} decision steps)",
        f"- Compressor: rule_fast via `HiveStack.compress`",
        f"- Machine: {result['machine']}",
        f"- Commit: `{result['commit']}` — {result['timestamp']}",
        "",
        "## Overall",
        "",
        "| Metric | Raw transcript | Compressed transcript |",
        "|---|---|---|",
        f"| Step accuracy (tool picked correctly) | {raw['step_accuracy_pct']:.1f}% | "
        f"**{comp['step_accuracy_pct']:.1f}%** |",
        f"| Episodes fully resolved | {raw['resolve_rate_pct']:.1f}% | "
        f"{comp['resolve_rate_pct']:.1f}% |",
        f"| Avg prompt tokens / episode | {raw['avg_prompt_tokens_per_episode']:.0f} | "
        f"{comp['avg_prompt_tokens_per_episode']:.0f} (**-{saved:.1f}%**) |",
        "",
        "## Per episode (step accuracy)",
        "",
        "| Episode | Raw | Compressed |",
        "|---|---|---|",
    ]
    for ep in EPISODES:
        raw_row = next(
            r for r in result["rows"]
            if r["episode_id"] == ep.id and r["condition"] == "raw"
        )
        comp_row = next(
            r for r in result["rows"]
            if r["episode_id"] == ep.id and r["condition"] == "compressed"
        )
        lines.append(
            f"| {ep.id} | {raw_row['step_accuracy_pct']:.0f}% "
            f"({raw_row['correct_steps']}/{raw_row['steps']}) | "
            f"{comp_row['step_accuracy_pct']:.0f}% "
            f"({comp_row['correct_steps']}/{comp_row['steps']}) |"
        )
    lines += [
        "",
        "## How to read this",
        "",
        "- **Step accuracy** = fraction of turns where the model picked the",
        "  right next tool. One wrong turn derails the episode.",
        "- **Resolve rate** = episodes where every step was correct (the",
        "  SWE-bench-style metric at this scale).",
        "- Compressed ≥ raw means Hive compression helps or is neutral on",
        "  multi-step navigation; compressed < raw is the measured cost.",
        "",
        "## Caveats",
        "",
        "- Fixed episodes with obvious next tools; not open-ended bug fixing.",
        "- Small CPU model; absolute numbers are a lower bound. The",
        "  raw-vs-compressed comparison is the signal.",
        "",
        "## Reproduce",
        "",
        "```bash",
        "pip install torch transformers",
        "python3 scripts/agent_loop_eval.py",
        "```",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-new-tokens", type=int, default=32)
    parser.add_argument(
        "--json", type=Path, default=REPO_ROOT / "results" / "agent_loop_eval.json"
    )
    parser.add_argument(
        "--report", type=Path, default=REPO_ROOT / "docs" / "benchmarks" / "agent-loop.md"
    )
    args = parser.parse_args(argv)

    stack = HiveStack()
    backend = make_backend(args.model)
    rows: list[dict[str, Any]] = []

    print(f"backend: {backend.name}; {len(EPISODES)} episodes x 2 conditions")
    for ep in EPISODES:
        for condition in ("raw", "compressed"):
            row = run_episode(
                ep,
                condition=condition,
                stack=stack,
                backend=backend,
                max_new_tokens=args.max_new_tokens,
            )
            rows.append(row)
            print(
                f"  {ep.id:22s} {condition:10s} "
                f"{row['correct_steps']}/{row['steps']} steps "
                f"({row['total_prompt_tokens']} tok)",
                flush=True,
            )

    result: dict[str, Any] = {
        "backend": backend.name,
        "episodes": len(EPISODES),
        "total_steps": sum(len(ep.steps) for ep in EPISODES),
        "aggregate": aggregate(rows),
        "machine": f"{platform.machine()} / {platform.system()} / {os.cpu_count()} cores",
        "commit": _git_commit(),
        "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "rows": rows,
    }

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(result, indent=2) + "\n")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_report(result))

    raw = result["aggregate"]["raw"]
    comp = result["aggregate"]["compressed"]
    print(f"\nstep accuracy raw:        {raw['step_accuracy_pct']:.1f}%")
    print(f"step accuracy compressed: {comp['step_accuracy_pct']:.1f}%")
    print(f"resolve rate raw:         {raw['resolve_rate_pct']:.1f}%")
    print(f"resolve rate compressed:  {comp['resolve_rate_pct']:.1f}%")
    print(f"json:                     {args.json}")
    print(f"report:                   {args.report}")
    return result


if __name__ == "__main__":
    main()
