"""hive-trace-bench — tool-choice coverage on deidentified real-agent traces.

Answers the actual product question: *what fraction of real agent tool
decisions can a CPU model take over, and how often is it right?*

Each trace in ``benchmarks/traces/`` is a real session's decision sequence
extracted by ``scripts/extract_traces.py`` — no user text, paths, or arg
values. Per step the policy sees the recorded state and predicts a tool.

Two coverage bounds are reported, because deidentified steps carry no
argument values:

* **tool-choice** — every confident prediction counts as routed; fidelity
  is the fraction matching the tool the agent actually used. This is the
  ceiling: how often the CPU picks the right tool.
* **executable** — only no-arg tools count as routed (``list_files``,
  ``run_tests``, ``finish``). Predicting ``write_file``/``read_file``/
  ``run_command``/``web``/``other`` counts as an escalation, exactly as
  ``predict()`` behaves in production when args can't be resolved from
  state. This is the floor: calls the CPU could fully execute today.

Trained with stratified k-fold CV (every trace is scored held-out).
Baselines (majority class, repeat-last) for context.

Two intervals are reported per aggregate:

* ``agreement_ci95`` — the naive Wilson interval over individual steps. It is
  kept for continuity with earlier artifacts but is *wrong* as an uncertainty
  estimate: steps within a trace come from one session and are highly
  correlated, so ~11k steps are not ~11k independent draws and the interval
  is far too narrow.
* ``agreement_ci95_cluster`` — a cluster-robust interval that treats each
  trace as one cluster (CR0 sandwich estimator over per-trace sums). This is
  the honest uncertainty number.

Usage:
    python scripts/trace_bench.py --traces benchmarks/traces \
        --output docs/benchmarks/trace-coverage.json
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_log = logging.getLogger("hive.trace_bench")

# Tools whose args are derivable from deidentified state (i.e. none needed).
EXECUTABLE_TOOLS = {"list_files", "run_tests", "finish"}

# thr=0.0 = raw argmax accuracy (every step routed, confidence ignored).
SWEEP = (0.0, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75)


def load_traces(trace_dir: Path) -> list[dict[str, Any]]:
    suite = json.loads((trace_dir / "suite.json").read_text())
    return [json.loads((trace_dir / f"{tid}.json").read_text())
            for tid in suite["tasks"]]


def trace_rows(trace: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for i, s in enumerate(trace["steps"]):
        state = {k: v for k, v in s.items() if k != "tool"}
        if i >= 2:
            state["prev2_tool"] = trace["steps"][i - 1]["last_tool"]
        rows.append({"state": state, "tool": s["tool"], "ok": True})
    return rows


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round((c - m) / d, 4), round((c + m) / d, 4)


def cluster_ci(cluster_sums: list[tuple[int, int]], z: float = 1.96) -> tuple[float, float]:
    """Cluster-robust 95% CI for the pooled mean of a per-step binary outcome.

    ``cluster_sums`` is one ``(sum_of_outcomes, n_steps)`` per trace. The
    variance of the pooled estimate is estimated from between-trace
    dispersion of the trace sums (CR0 sandwich), so correlated steps inside
    a trace cannot inflate the effective sample size the way the naive
    Wilson interval assumes.
    """
    n = sum(n for _, n in cluster_sums)
    g = len(cluster_sums)
    if n == 0 or g < 2:
        return 0.0, 0.0
    p = sum(s for s, _ in cluster_sums) / n
    var = sum((s - p * m) ** 2 for s, m in cluster_sums) / (n * n)
    m = z * math.sqrt(var)
    return round(max(0.0, p - m), 4), round(min(1.0, p + m), 4)

def collect_records(policy: Any, test_traces: list[dict[str, Any]],
                    baseline: str | None = None,
                    majority: str = "read_file") -> list[dict[str, Any]]:
    """One record per held-out step: (family, conf, predicted, actual)."""
    records = []
    for tr in test_traces:
        for i, s in enumerate(tr["steps"]):
            state = {k: v for k, v in s.items() if k != "tool"}
            # Observable context the deidentified record omits: the tool two
            # steps back (t-1's tool is already `last_tool`).
            if i >= 2:
                state["prev2_tool"] = tr["steps"][i - 1]["last_tool"]
            if baseline == "majority":
                tool, conf = majority, 1.0
            elif baseline == "repeat-last":
                tool, conf = state.get("last_tool") or majority, 1.0
            else:
                tool, conf = None, 0.0  # filled by batch predict below
            records.append({"family": tr["family"], "trace": tr["id"],
                            "conf": conf, "pred": tool, "actual": s["tool"],
                            "state": state if baseline is None else None})
    if baseline is None and records:
        # One vectorized predict_proba over all steps — single-sample
        # predicts are ~100x slower for ensembles.
        from hive.cpu_policy import _MarkovModel, featurize
        clf = policy.clf
        if isinstance(clf, _MarkovModel):
            probs = [clf.predict_proba_state(r["state"]) for r in records]
        else:
            probs = clf.predict_proba([featurize(r["state"])
                                       for r in records])
        classes = list(policy.classes_)
        for r, p in zip(records, probs, strict=True):
            idx = int(np.argmax(p))
            r["pred"], r["conf"] = classes[idx], float(p[idx])
        for r in records:
            r.pop("state")
    return records


def aggregate(records: list[dict[str, Any]], threshold: float,
              executable_only: bool) -> dict[str, Any]:
    per_fam: dict[str, Counter] = {}
    tot = Counter()
    # Per-trace (sum, n) of the agreement outcome, for the cluster-robust CI.
    clusters: dict[str, list[int]] = {}
    fam_clusters: dict[str, dict[str, list[int]]] = {}
    for r in records:
        routed = (r["pred"] is not None and r["conf"] >= threshold
                  and (not executable_only or r["pred"] in EXECUTABLE_TOOLS))
        ok = int(bool(routed) and r["pred"] == r["actual"])
        for c in (tot, per_fam.setdefault(r["family"], Counter())):
            c["steps"] += 1
            if routed:
                c["routed"] += 1
                if r["pred"] == r["actual"]:
                    c["matched"] += 1
        trace = str(r.get("trace", ""))
        cl = clusters.setdefault(trace, [0, 0])
        cl[0] += ok
        cl[1] += 1
        fcl = fam_clusters.setdefault(r["family"], {}).setdefault(trace, [0, 0])
        fcl[0] += ok
        fcl[1] += 1

    def pack(c: Counter, sums: list[tuple[int, int]]) -> dict[str, Any]:
        steps, routed, matched = c["steps"], c["routed"], c["matched"]
        lo, hi = wilson(matched, steps)
        clo, chi = cluster_ci(sums)
        return {"steps": steps, "routed": routed, "matched": matched,
                "coverage": round(routed / steps, 4) if steps else 0,
                "fidelity": round(matched / routed, 4) if routed else 0,
                "agreement": round(matched / steps, 4) if steps else 0,
                "agreement_ci95": [lo, hi],
                "agreement_ci95_cluster": [clo, chi],
                "n_traces": len(sums)}

    return {"overall": pack(tot, [tuple(v) for v in clusters.values()]),
            "per_family": {f: pack(c, [tuple(v) for v in fam_clusters[f].values()])
                           for f, c in sorted(per_fam.items())}}


def _store(name: str, recs: list[dict[str, Any]], threshold: float,
           report: dict[str, Any]) -> None:
    a0 = aggregate(recs, 0.0, False)["overall"]
    report["models"][name] = {"argmax_overall": a0,
                              "tool_choice": aggregate(recs, threshold, False),
                              "executable": aggregate(recs, threshold, True)}


def _emit(name: str, recs: list[dict[str, Any]], threshold: float,
          report: dict[str, Any]) -> None:
    _store(name, recs, threshold, report)
    a0 = report["models"][name]["argmax_overall"]
    tc = report["models"][name]["tool_choice"]
    ex = report["models"][name]["executable"]
    print(f"[{name:>11}] argmax={a0['agreement']*100:5.1f}% | @thr={threshold}: "
          f"cov={tc['overall']['coverage']*100:5.1f}% "
          f"fid={tc['overall']['fidelity']*100:5.1f}% "
          f"agree={tc['overall']['agreement']*100:5.1f}% "
          f"CI95={tc['overall']['agreement_ci95']} "
          f"CI95cl={tc['overall']['agreement_ci95_cluster']} | "
          f"exec-cov={ex['overall']['coverage']*100:5.1f}%")

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--traces", default="benchmarks/traces",
                    help="eval suite (held-out when --pool is given)")
    ap.add_argument("--pool", default=None,
                    help="larger trace corpus to train on; eval suite stays "
                         "held out (ids excluded from training)")
    ap.add_argument("--algorithms", default="rf,rf-deep,extratrees,hgb,logreg,"
                    "mlp,markov1,markov2")
    ap.add_argument("--curve", action="store_true",
                    help="learning curve: argmax agreement vs train fraction")
    ap.add_argument("--curve-only", action="store_true",
                    help="skip bake-off fits; keep models already in --output "
                         "and (re)run only the learning curve")
    ap.add_argument("--curve-algos", default="rf,hgb,logreg,markov1,markov2",
                    help="algorithms included in --curve (fast ones by default)")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--threshold", type=float, default=0.45)
    ap.add_argument("--output", default=None)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import random

    from hive.cpu_policy import TRACE_TOOLS, CPURouterPolicy

    traces = load_traces(Path(args.traces))
    algos = [a.strip() for a in args.algorithms.split(",") if a.strip()]
    _log.info("%d eval traces, families=%s, algos=%s", len(traces),
              Counter(t["family"] for t in traces), algos)

    records_by_model: dict[str, list[dict[str, Any]]] = {}
    report: dict[str, Any] = {
        "n_traces": len(traces), "threshold": args.threshold,
        "families": dict(sorted(Counter(t["family"] for t in traces).items())),
        "models": {},
    }
    out_path = Path(args.output) if args.output else None
    if args.curve_only and out_path and out_path.exists():
        prior = json.loads(out_path.read_text())
        report["models"] = prior.get("models", {})
        _log.info("curve-only: keeping %d prior model results",
                  len(report["models"]))

    def checkpoint() -> None:
        # Long bake-offs only wrote the artifact at the very end — a kill
        # lost hours. Persist after every model so partial results survive.
        for name, recs in records_by_model.items():
            _store(name, recs, args.threshold, report)
        if out_path:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, indent=2) + "\n")

    if args.pool:
        eval_ids = {t["id"] for t in traces}
        pool = [json.loads(p.read_text())
                for p in sorted(Path(args.pool).glob("*.json"))
                if p.name != "suite.json"]
        train_traces = [t for t in pool if t["id"] not in eval_ids]
        _log.info("train pool=%d traces (eval ids excluded)", len(train_traces))
        train_rows = [r for t in train_traces for r in trace_rows(t)]
        maj = Counter(r["tool"] for r in train_rows).most_common(1)[0][0]
        for algo in [] if args.curve_only else algos:
            _log.info("fit %s on %d rows", algo, len(train_rows))
            policy = CPURouterPolicy(threshold=0.0, tools=TRACE_TOOLS,
                                     algorithm=algo)
            policy.fit(train_rows)
            records_by_model[algo] = collect_records(policy, traces)
            checkpoint()
        records_by_model["majority"] = collect_records(
            None, traces, baseline="majority", majority=maj)
        records_by_model["repeat-last"] = collect_records(
            None, traces, baseline="repeat-last", majority=maj)
        checkpoint()

        if args.curve:
            rng = random.Random(args.seed)
            fracs = (0.02, 0.05, 0.1, 0.25, 0.5, 1.0)
            curve_algos = [a.strip() for a in args.curve_algos.split(",")
                           if a.strip() in algos]
            curve: dict[str, dict[str, float]] = {}
            for algo in curve_algos:
                curve[algo] = {}
                for f in fracs:
                    k = max(1, int(len(train_traces) * f))
                    sub = rng.sample(train_traces, k)
                    rows = [r for t in sub for r in trace_rows(t)]
                    pol = CPURouterPolicy(threshold=0.0, tools=TRACE_TOOLS,
                                          algorithm=algo)
                    pol.fit(rows)
                    recs = collect_records(pol, traces)
                    curve[algo][str(f)] = aggregate(
                        recs, 0.0, False)["overall"]["agreement"]
                    _log.info("curve %s @%.2f (%d traces): %.3f",
                              algo, f, k, curve[algo][str(f)])
                    report["learning_curve_argmax_agreement"] = curve
                    checkpoint()
    else:
        folds: list[list[dict[str, Any]]] = [[] for _ in range(args.folds)]
        by_fam: dict[str, list[dict[str, Any]]] = {}
        for t in traces:
            by_fam.setdefault(t["family"], []).append(t)
        for fam_traces in by_fam.values():
            for i, t in enumerate(fam_traces):
                folds[i % args.folds].append(t)
        for k in range(args.folds):
            test = folds[k]
            train = [t for i, f in enumerate(folds) if i != k for t in f]
            train_rows = [r for t in train for r in trace_rows(t)]
            maj = Counter(r["tool"] for r in train_rows).most_common(1)[0][0]
            for algo in algos:
                policy = CPURouterPolicy(threshold=0.0, tools=TRACE_TOOLS,
                                         algorithm=algo)
                policy.fit(train_rows)
                records_by_model.setdefault(algo, [])
                records_by_model[algo] += collect_records(policy, test)
            records_by_model.setdefault("majority", [])
            records_by_model["majority"] += collect_records(
                None, test, baseline="majority", majority=maj)
            records_by_model.setdefault("repeat-last", [])
            records_by_model["repeat-last"] += collect_records(
                None, test, baseline="repeat-last", majority=maj)

    print()
    for name, recs in records_by_model.items():
        _emit(name, recs, args.threshold, report)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
        _log.info("wrote %s", args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
