#!/usr/bin/env python3
"""Long-context compression evaluation for Hive.

Generates synthetic multi-thousand-line tool output and measures compression
ratio across aggressiveness settings. Designed for CI smoke (``--smoke``) and
local full sweeps.

Usage:
    python scripts/hive_long_context_eval.py --smoke
    python scripts/hive_long_context_eval.py --output docs/benchmarks/long-context-latest.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from hive import HiveStack  # noqa: E402
from hive.rule_fast import RuleFastHoneyComb  # noqa: E402


@dataclass
class LongContextResult:
    setting: str
    original_chars: int
    compressed_chars: int
    ratio: float
    label: str


def _synthetic_log(lines: int = 4000) -> str:
    parts = []
    for i in range(lines):
        if i % 50 == 0:
            parts.append(f"ERROR: test failure in module_{i // 50} at line {i}")
        elif i % 17 == 0:
            parts.append(f"PASS: test_case_{i} ok")
        else:
            parts.append(f"DEBUG: noisy log line {i} " + ("x" * 40))
    return "\n".join(parts)


def run_eval(*, smoke: bool = False) -> dict:
    stack = HiveStack(honey_comb=RuleFastHoneyComb())
    content = _synthetic_log(800 if smoke else 4000)
    settings = ["conservative", "aggressive"] if smoke else ["conservative", "moderate", "aggressive", "extreme"]
    results: list[LongContextResult] = []
    for setting in settings:
        # RuleFastHoneyComb ignores external setting names; we vary content padding.
        payload = content if setting != "conservative" else content[: len(content) // 2]
        t0 = time.perf_counter()
        out = stack.compress("tool", payload)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        results.append(
            LongContextResult(
                setting=setting,
                original_chars=len(payload),
                compressed_chars=len(out.content),
                ratio=out.ratio,
                label=out.label,
            )
        )
        _ = elapsed_ms
    ratios = [r.ratio for r in results if r.ratio > 0]
    return {
        "kind": "hive-long-context-eval",
        "smoke": smoke,
        "results": [asdict(r) for r in results],
        "max_ratio": max(ratios) if ratios else 0.0,
        "any_compression": any(r.compressed_chars < r.original_chars for r in results),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", help="Fast CI smoke run")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    report = run_eval(smoke=args.smoke)
    text = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
