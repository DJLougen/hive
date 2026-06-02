"""hive_benchmark_micro.py — per-component micro-benchmarks.

The macro benchmark in :mod:`hive_benchmark` runs the full stack under
one wall-clock. The micro benchmarks here isolate each component, so a
regression points at a single thing rather than a tangled aggregate.

Run::

    python scripts/hive_benchmark_micro.py

Each component is reported with rate mean and stdev across ``--runs``
(default 5) invocations. The script also auto-selects the best honey-comb
mode (rule_fast vs honey-comb) so the comparison is fair.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import statistics
import string
import time
from typing import Any

# We import the meta-package + benchmarks lazily.
import hive  # noqa: F401
from hive.rust_brain import RustBrain

_log = logging.getLogger("hive.benchmark.micro")


# ---------------------------------------------------------------------------
# Workload builders
# ---------------------------------------------------------------------------


def _synth(rng: random.Random) -> str:
    """Realistic-looking test-output / file content."""
    n = rng.randint(120, 480)
    if rng.random() < 0.5:
        return "\n".join(
            f"  test_{rng.randrange(1000):04d} ... {rng.choice(['ok', 'FAIL', 'ERROR'])}"
            for _ in range(max(1, n // 4))
        )
    return " ".join(
        "".join(rng.choices(string.ascii_letters, k=rng.randint(3, 8)))
        for _ in range(n)
    )


def make_turns(n: int, *, seed: int) -> list[tuple[str, str]]:
    rng = random.Random(seed)
    roles = ["system", "user", "assistant", "tool"]
    return [(roles[i % 4], _synth(rng)) for i in range(n)]


# ---------------------------------------------------------------------------
# Component runners
# ---------------------------------------------------------------------------


def bench_rust_brain(writes: int, *, runs: int) -> dict[str, Any]:
    """Wall-clock and rate for ``writes`` writes + tail read."""
    timings: list[float] = []
    for _ in range(runs):
        brain = RustBrain()
        t0 = time.perf_counter()
        for i in range(writes):
            brain.remember(f"k{i:04d}", {"i": i, "rand": random.random()})
        for i in range(0, writes, max(1, writes // 100)):
            brain.recall(f"k{i:04d}")
        timings.append(time.perf_counter() - t0)
    return _summarise(timings, total_items=writes, name="rust_brain")


def bench_compress(turns: list[tuple[str, str]], *, runs: int, mode: str) -> dict[str, Any]:
    if mode == "fast":
        from hive.rule_fast import RuleFastHoneyComb
        comb: Any = RuleFastHoneyComb()
    else:
        from honeycomb import HoneyComb  # type: ignore[import-not-found]
        comb = HoneyComb(thread_safe=True, metrics_enabled=True)

    timings: list[float] = []
    for _ in range(runs):
        if mode != "fast":
            comb = type(comb)(thread_safe=True, metrics_enabled=True)
        t0 = time.perf_counter()
        if mode == "fast":
            from hive.rule_fast import Message as Msg
        else:
            from honeycomb import Message as Msg
        for role, content in turns:
            comb.process(Msg(role=role, content=content))
        timings.append(time.perf_counter() - t0)
    return _summarise(timings, total_items=len(turns), name=f"compress[{mode}]")


def bench_busybee(states: list[dict[str, Any]], *, runs: int) -> dict[str, Any]:
    from busybee_cpu import CpuActionPolicy  # type: ignore[import-not-found]

    # Train a small policy on the synthetic states themselves.
    # Train a small policy on the synthetic states. We rotate through
    # the 4 actions and attach the matching default args so the template
    # model has 4 distinct labels to learn (otherwise sklearn raises
    # "got 1 class").
    from busybee_cpu.templates import DEFAULT_ARGS  # type: ignore

    actions = ["read_file", "run_tests", "apply_patch", "escalate"]
    rows = []
    for i, s in enumerate(states):
        s = dict(s)
        tool = actions[i % len(actions)]
        s["target_action"] = {"tool": tool, "args": dict(DEFAULT_ARGS[tool])}
        rows.append(s)
    policy = CpuActionPolicy.train(rows, augment=False)
    timings: list[float] = []
    for _ in range(runs):
        t0 = time.perf_counter()
        for s in states:
            policy.predict(dict(s))
        timings.append(time.perf_counter() - t0)
    return _summarise(timings, total_items=len(states), name="busybee_cpu")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _summarise(timings: list[float], *, total_items: int, name: str) -> dict[str, Any]:
    rates = [total_items / max(t, 1e-9) for t in timings]
    return {
        "name": name,
        "runs": len(timings),
        "items": total_items,
        "duration_s_mean": statistics.fmean(timings),
        "duration_s_stdev": statistics.pstdev(timings) if len(timings) > 1 else 0.0,
        "rate_mean_per_s": statistics.fmean(rates),
        "rate_stdev_per_s": statistics.pstdev(rates) if len(rates) > 1 else 0.0,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Per-component micro-benchmarks.")
    p.add_argument("--turns", type=int, default=500)
    p.add_argument("--brain-writes", type=int, default=2000)
    p.add_argument("--busybee-states", type=int, default=200)
    p.add_argument("--runs", type=int, default=5)
    p.add_argument("--output", default=None)
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    turns = make_turns(args.turns, seed=0)
    states = [
        {
            "goal": "ship hive",
            "state": {"current_step": i, "last_tool": "read_file", "recent_observations": ["a"]},
            "available_tools": [
                {"name": "read_file"},
                {"name": "run_tests"},
                {"name": "apply_patch"},
                {"name": "escalate"},
            ],
        }
        for i in range(args.busybee_states)
    ]

    out: list[dict[str, Any]] = []
    out.append(bench_rust_brain(args.brain_writes, runs=args.runs))
    out.append(bench_compress(turns, runs=args.runs, mode="fast"))
    try:
        out.append(bench_compress(turns, runs=args.runs, mode="honeycomb"))
    except Exception as exc:
        out.append({"name": "compress[honeycomb]", "skipped": str(exc)})
    try:
        out.append(bench_busybee(states, runs=args.runs))
    except Exception as exc:
        out.append({"name": "busybee_cpu", "skipped": str(exc)})

    print("\n=== Hive per-component micro-benchmark ===")
    print(f"  {'component':<24} {'items':>8} {'time(s)':>14} {'rate':>14}")
    print(f"  {'-'*24} {'-'*8} {'-'*14} {'-'*14}")
    for row in out:
        if "skipped" in row:
            print(f"  {row['name']:<24} (skipped: {row['skipped']})")
            continue
        print(
            f"  {row['name']:<24} {row['items']:>8d} "
            f"{row['duration_s_mean']:>10.4f} ± {row['duration_s_stdev']:>6.4f} "
            f"{row['rate_mean_per_s']:>10.1f} ± {row['rate_stdev_per_s']:>6.1f}/s"
        )

    if args.output:
        out_path = os.path.abspath(args.output)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump({"runs": args.runs, "components": out}, fh, indent=2)
        print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
