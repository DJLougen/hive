#!/usr/bin/env python3
"""Sustained-load test for Hive enterprise deployments.

Validates throughput and latency under continuous load, not just
micro-benchmarks. Reports p50/p95/p99 latencies and error rates.

Usage::

    python scripts/hive_load_test.py --duration 60 --rps 1000
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from typing import Any

from hive import HiveStack
from hive.rule_fast import RuleFastHoneyComb


def _run_load(
    stack: HiveStack,
    *,
    duration_s: float,
    target_rps: int,
    operations: list[str],
) -> dict[str, Any]:
    """Fire operations at target RPS for duration_s seconds."""
    interval = 1.0 / target_rps if target_rps > 0 else 0.0
    latencies: dict[str, list[float]] = {op: [] for op in operations}
    errors: dict[str, int] = {op: 0 for op in operations}
    counts: dict[str, int] = {op: 0 for op in operations}

    t_end = time.perf_counter() + duration_s
    next_call = time.perf_counter()

    while time.perf_counter() < t_end:
        for op in operations:
            if time.perf_counter() >= t_end:
                break
            t0 = time.perf_counter()
            try:
                if op == "route":
                    stack.route({"goal": "ship", "available_tools": [{"name": "read_file"}]})
                elif op == "compress":
                    stack.compress("tool", "tests: 12 passed, 2 failed")
                elif op == "remember":
                    stack.remember(f"k{counts[op]}", f"v{counts[op]}")
                elif op == "recall":
                    stack.recall(f"k{counts[op]}")
            except Exception:
                errors[op] += 1
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            latencies[op].append(elapsed_ms)
            counts[op] += 1

        next_call += interval * len(operations)
        sleep_time = next_call - time.perf_counter()
        if sleep_time > 0:
            time.sleep(sleep_time)

    results: dict[str, Any] = {}
    for op in operations:
        times = sorted(latencies[op])
        n = len(times)
        results[op] = {
            "count": n,
            "errors": errors[op],
            "error_rate_pct": round(errors[op] / max(n, 1) * 100, 2),
            "p50_ms": round(times[n // 2], 3) if n > 0 else 0.0,
            "p95_ms": round(times[int(n * 0.95)] if n >= 20 else times[-1], 3) if n > 0 else 0.0,
            "p99_ms": round(times[int(n * 0.99)] if n >= 100 else times[-1], 3) if n > 0 else 0.0,
            "throughput_per_s": round(n / duration_s, 1),
        }
    return results


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Sustained-load test for Hive")
    p.add_argument("--duration", type=int, default=10, help="Duration in seconds")
    p.add_argument("--rps", type=int, default=100, help="Target operations per second")
    p.add_argument("--ops", default="route,compress,remember,recall", help="Comma-separated ops")
    p.add_argument("--output", default=None, help="JSON output file")
    p.add_argument("--quiet", action="store_true", help="Suppress console output")
    args = p.parse_args(argv)

    stack = HiveStack(honey_comb=RuleFastHoneyComb())
    ops = [o.strip() for o in args.ops.split(",")]

    if not args.quiet:
        print(f"Load test: {args.duration}s at ~{args.rps} ops/s")
        print(f"Operations: {ops}")

    results = _run_load(stack, duration_s=args.duration, target_rps=args.rps, operations=ops)

    if not args.quiet:
        print(f"\n{'op':<12} {'count':>8} {'err%':>6} {'p50ms':>8} {'p95ms':>8} {'p99ms':>8} {'ops/s':>8}")
        print("-" * 70)
        for op, r in results.items():
            print(
                f"{op:<12} {r['count']:>8} {r['error_rate_pct']:>6.2f} "
                f"{r['p50_ms']:>8.3f} {r['p95_ms']:>8.3f} {r['p99_ms']:>8.3f} "
                f"{r['throughput_per_s']:>8.1f}"
            )

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2)
        if not args.quiet:
            print(f"\nWrote {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
