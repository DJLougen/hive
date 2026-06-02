#!/usr/bin/env python3
"""Chaos engineering: failure injection for Hive.

Validates resilience by randomly killing backends, adding latency,
and corrupting state. Run against a staging stack before production.

Usage::

    python scripts/hive_chaos.py --mode latency --magnitude 0.1 --duration 30
"""

from __future__ import annotations

import argparse
import random
import time
from typing import Any

from hive import HiveStack
from hive.rule_fast import RuleFastHoneyComb


class ChaosMonkey:
    """Inject failures into a HiveStack for resilience testing."""

    def __init__(self, stack: HiveStack, *, seed: int = 42) -> None:
        self.stack = stack
        self.rng = random.Random(seed)
        self.injected = 0

    def inject_latency(self, magnitude_s: float) -> None:
        """Add random latency to the next operation."""
        delay = self.rng.uniform(0, magnitude_s)
        time.sleep(delay)
        self.injected += 1

    def corrupt_state(self, state: dict[str, Any]) -> dict[str, Any]:
        """Randomly mutate a state dict to simulate bad input."""
        if self.rng.random() < 0.3:
            state["goal"] = state.get("goal", "") * 100  # Blow up length
        if self.rng.random() < 0.2:
            state["step"] = -999  # Invalid negative
        return state

    def drop_request(self, probability: float) -> bool:
        """Return True with given probability (simulate network drop)."""
        return self.rng.random() < probability


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Chaos engineering for Hive")
    p.add_argument("--mode", choices=["latency", "corrupt", "drop", "all"], default="latency")
    p.add_argument("--magnitude", type=float, default=0.05, help="Latency magnitude in seconds")
    p.add_argument("--duration", type=int, default=10, help="Duration in seconds")
    p.add_argument("--drop-rate", type=float, default=0.1, help="Request drop probability")
    args = p.parse_args(argv)

    stack = HiveStack(honey_comb=RuleFastHoneyComb())
    monkey = ChaosMonkey(stack)

    errors = 0
    total = 0
    t_end = time.perf_counter() + args.duration

    while time.perf_counter() < t_end:
        state = {"goal": "ship", "available_tools": [{"name": "read_file"}]}
        total += 1

        if args.mode in ("latency", "all"):
            monkey.inject_latency(args.magnitude)

        if args.mode in ("corrupt", "all"):
            state = monkey.corrupt_state(state)

        if args.mode in ("drop", "all"):
            if monkey.drop_request(args.drop_rate):
                continue

        try:
            stack.route(state)
        except Exception:
            errors += 1

    print(f"Chaos test complete: {total} ops, {errors} errors ({errors/total*100:.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
