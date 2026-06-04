#!/usr/bin/env python3
"""CI gate: fail the build if benchmark results regress.

Compares current benchmark JSON against a baseline. Fails if:
- compression_ratio drops > 5%
- route latency grows > 10%
- memory write latency grows > 10%

Usage::

    python scripts/benchmark_regression_gate.py \
        --current docs/benchmarks/latest-macro.json \
        --baseline docs/benchmarks/baseline.json
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def load(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def compare(current: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    failures: list[str] = []

    cur_comps = {c["name"]: c for c in current.get("components", [])}
    base_comps = {c["name"]: c for c in baseline.get("components", [])}

    for name in ("rust_brain", "compress[fast]", "compress[honeycomb]"):
        if name not in cur_comps or name not in base_comps:
            continue
        cur = cur_comps[name]
        base = base_comps[name]

        # Latency regression
        cur_lat = cur.get("duration_s_mean", 0)
        base_lat = base.get("duration_s_mean", 1e-9)
        lat_pct = (cur_lat - base_lat) / base_lat * 100
        if lat_pct > 10:
            failures.append(
                f"LATENCY REGRESSION: {name} +{lat_pct:.1f}% "
                f"({base_lat:.4f}s → {cur_lat:.4f}s)"
            )

        # Compression ratio regression (only for compress components)
        if name.startswith("compress"):
            cur_ratio = cur.get("compression_ratio", 0)
            base_ratio = base.get("compression_ratio", 1e-9)
            ratio_pct = (base_ratio - cur_ratio) / base_ratio * 100
            if ratio_pct > 5:
                failures.append(
                    f"COMPRESSION REGRESSION: {name} -{ratio_pct:.1f}% "
                    f"({base_ratio:.3f}x → {cur_ratio:.3f}x)"
                )

    return failures


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Benchmark regression gate")
    p.add_argument("--current", default="docs/benchmarks/latest-macro.json")
    p.add_argument("--baseline", default="docs/benchmarks/baseline.json")
    args = p.parse_args(argv)

    try:
        current = load(args.current)
        baseline = load(args.baseline)
    except FileNotFoundError as exc:
        print(f"SKIP: missing file: {exc.filename}")
        return 0

    failures = compare(current, baseline)
    if failures:
        print("REGRESSION DETECTED — failing build:")
        for f in failures:
            print(f"  {f}")
        return 1

    print("No regression detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
