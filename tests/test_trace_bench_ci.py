"""Cluster-aware interval tests for scripts/trace_bench.py — no LLM calls."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.trace_bench import aggregate, cluster_ci, wilson  # noqa: E402


def _records(per_trace_ok: list[tuple[int, int]]) -> list[dict]:
    """Synthetic records: (matched, steps) per trace, all routed at thr 0."""
    recs = []
    for i, (matched, steps) in enumerate(per_trace_ok):
        for j in range(steps):
            hit = j < matched
            recs.append({"family": "f", "trace": f"t{i}", "conf": 1.0,
                         "pred": "read_file" if hit else "write_file",
                         "actual": "read_file"})
    return recs


def test_cluster_interval_wider_than_naive_on_correlated_steps():
    # 20 traces x 50 steps; each trace is internally homogeneous (all-hit or
    # all-miss) — the extreme of within-trace correlation. The naive Wilson
    # interval treats 1000 steps as independent and is far too narrow.
    recs = _records([(50, 50)] * 10 + [(0, 50)] * 10)
    out = aggregate(recs, 0.0, False)["overall"]
    naive_lo, naive_hi = out["agreement_ci95"]
    cl_lo, cl_hi = out["agreement_ci95_cluster"]
    assert out["agreement"] == 0.5          # point estimate unchanged
    assert out["n_traces"] == 20
    assert (cl_hi - cl_lo) > (naive_hi - naive_lo)
    # Measured on this fixture: naive width 0.0618 (the Wilson interval for
    # 500/1000 treated as independent), cluster width 0.4382.
    assert naive_hi - naive_lo < 0.07       # naive is suspiciously tight
    assert cl_hi - cl_lo > 0.3              # cluster interval reflects 20 draws


def test_cluster_interval_matches_naive_when_traces_are_singletons():
    # One step per trace = independent draws; both intervals should agree.
    recs = _records([(1, 1)] * 40 + [(0, 1)] * 40)
    out = aggregate(recs, 0.0, False)["overall"]
    naive_lo, naive_hi = out["agreement_ci95"]
    cl_lo, cl_hi = out["agreement_ci95_cluster"]
    assert abs((cl_hi - cl_lo) - (naive_hi - naive_lo)) < 0.05


def test_cluster_ci_degenerate_inputs():
    assert cluster_ci([]) == (0.0, 0.0)
    assert cluster_ci([(5, 10)]) == (0.0, 0.0)  # a single cluster: no dispersion


def test_wilson_unchanged():
    assert wilson(0, 0) == (0.0, 0.0)
    lo, hi = wilson(50, 100)
    assert lo < 0.5 < hi
