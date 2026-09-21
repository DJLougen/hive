#!/usr/bin/env python3
"""Claim gate — every number Hive publishes must match a committed artifact.

Each registered check pulls one number out of README.md with a regex and compares it
against a value read from a committed benchmark artifact. Rounded display values are
accepted within tolerance (relative 1e-3, absolute 0.05).

Exit codes: 0 = all checks OK, 1 = at least one MISSING or MISMATCH.

Stdlib only; no network.
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README_PATH = ROOT / "README.md"
REL_TOL = 1e-3
ABS_TOL = 0.05

# --------------------------------------------------------------------------- #
# Registry
#
#   kind="pair"   -> readme groups: baseline, hive, delta (percent)
#   kind="single" -> readme group 1 vs the artifact value
# --------------------------------------------------------------------------- #

BENCH = "docs/benchmarks/hive-bench-flash-r3.json"  # 3 repeats x 10 tasks, both arms
BENCH_V1 = "docs/benchmarks/hive-bench-flash.json"  # superseded single-pass run, kept
MICRO = "docs/benchmarks/latest-micro.json"
SMOKE = "docs/benchmarks/long-context-smoke.json"
BAKEOFF = "docs/benchmarks/trace-bakeoff.json"
CPU_POLICY = "docs/benchmarks/hive-bench-cpu-policy.json"
CAPABILITY = "docs/benchmarks/hive-bench-capability.json"  # 5 repeats x 6 held-out tasks, 3 arms
HARD = "docs/benchmarks/hive-bench-hard.json"  # 15 repeats x 6 held-out tasks, 3 arms
BENCH_README = "benchmarks/README.md"  # all benchmark detail lives here now
TRAINED = "docs/benchmarks/hive-bench-hard-trained.json"  # hard tier, TRAINED policy (checkpoint provenance unverifiable)
NEW2 = "docs/benchmarks/hive-bench-hard-new2.json"  # 2-task ceiling annex, reported separately

CHECKS: list[dict] = [
    # ---- A1: real-workload A/B table (summary of hive-bench-flash.json) ----
    {
        "label": "A1 resolve rate (count)",
        "kind": "pair",
        "artifact": BENCH,
        "baseline": "summary.baseline.resolved",
        "hive": "summary.hive.resolved",
        "regex": r"^\| Resolve rate \| 100% \((30)/30\) \| 100% \((30)/30\) \| \*\*0 pp\*\*",
    },
    {
        "label": "A1 mean LLM calls",
        "kind": "pair",
        "artifact": BENCH,
        "baseline": "summary.baseline.mean_llm_calls",
        "hive": "summary.hive.mean_llm_calls",
        "delta_sign": -1,
        "regex": r"^\| Mean LLM calls \| ([\d.]+) \| ([\d.]+) \| \*\*[−-]([\d.]+)%\*\*",  # noqa: RUF001
    },
    {
        "label": "A1 mean prompt tokens",
        "kind": "pair",
        "artifact": BENCH,
        "baseline": "summary.baseline.mean_prompt_tokens",
        "hive": "summary.hive.mean_prompt_tokens",
        "delta_sign": -1,
        "regex": r"^\| Mean prompt tokens \| ([\d,]+) \| ([\d,]+) \| \*\*[−-]([\d.]+)%\*\*",  # noqa: RUF001
    },
    {
        "label": "A1 mean completion tokens",
        "kind": "pair",
        "artifact": BENCH,
        "baseline": "summary.baseline.mean_completion_tokens",
        "hive": "summary.hive.mean_completion_tokens",
        "delta_sign": -1,
        "regex": r"^\| Mean completion tokens \| ([\d,]+) \| ([\d,]+) \| \*\*[−-]([\d.]+)%\*\*",  # noqa: RUF001
    },
    {
        "label": "A1 mean turns",
        "kind": "pair",
        "artifact": BENCH,
        "baseline": "summary.baseline.mean_turns",
        "hive": "summary.hive.mean_turns",
        "delta_sign": 1,
        "regex": r"^\| Mean turns \| ([\d.]+) \| ([\d.]+) \| \+([\d.]+)%",
    },
    {
        "label": "A1 mean wall clock",
        "kind": "pair",
        "artifact": BENCH,
        "baseline": "summary.baseline.mean_wall_clock_s",
        "hive": "summary.hive.mean_wall_clock_s",
        "delta_sign": -1,
        "regex": r"^\| Mean wall clock \(s\) \| ([\d.]+) \| ([\d.]+) \| \*\*[−-]([\d.]+)%\*\*",  # noqa: RUF001
    },
    {
        "label": "A1 memory recall hits",
        "kind": "single",
        "artifact": BENCH,
        "path": "summary.hive.memory_hits",
        "regex": r"^\| Memory recall hits \| — \| (\d+)/30 \|",
    },
    # ---- A2: per-component throughput (latest-micro.json) ----
    {
        "label": "A2 rust_brain items/s",
        "kind": "single",
        "artifact": MICRO,
        "path": "components[rust_brain].rate_mean_per_s",
        "regex": r"^\| rust_brain \| ([\d,]+) \|",
    },
    {
        "label": "A2 compress[fast] items/s",
        "kind": "single",
        "artifact": MICRO,
        "path": "components[compress[fast]].rate_mean_per_s",
        "regex": r"^\| compress\[fast\] \| ([\d,]+) \|",
    },
    {
        "label": "A2 compress[honeycomb] items/s",
        "kind": "single",
        "artifact": MICRO,
        "path": "components[compress[honeycomb]].rate_mean_per_s",
        "regex": r"^\| compress\[honeycomb\] \| ([\d,]+) \|",
    },
    {
        "label": "A2 busybee_cpu items/s",
        "kind": "single",
        "artifact": MICRO,
        "path": "components[busybee_cpu].rate_mean_per_s",
        # published as a 3-significant-figure table value (112) vs 111.7 in prose
        "rel_tol": 5e-3,
        "regex": r"^\| busybee_cpu \| ([\d,]+) \|",
    },
    # ---- A3: long-context compression ratio ----
    {
        "label": "A3 long-context max_ratio",
        "kind": "single",
        "artifact": SMOKE,
        "path": "max_ratio",
        "regex": r"up to \*\*([\d.]+)×\*\* compression",  # noqa: RUF001
    },
    # ---- per-pass means, recomputed from the raw results list (not the summary) ----
    {
        "label": "cpu-policy pass 0 mean LLM calls (README)",
        "kind": "group_mean",
        "file": BENCH_README,
        "artifact": CPU_POLICY,
        "list": "results",
        "group_by": "pass_idx",
        "group": 0,
        "metric": "llm_calls",
        "regex": r"pass 0 = 10/10 resolved at ([\d.]+) mean LLM calls",
    },
    {
        "label": "cpu-policy pass 1 LLM calls (benchmarks/README.md)",
        "kind": "group_mean",
        "file": "benchmarks/README.md",
        "artifact": CPU_POLICY,
        "list": "results",
        "group_by": "pass_idx",
        "group": 1,
        "metric": "llm_calls",
        "regex": r"pass 1 = 10/10 resolved at\s*\*\*([\d.]+) LLM calls",
    },
    # ---- dispersion: the numbers a --repeat run exists to produce ----
    {
        "label": "baseline per-pass LLM-call stderr (README)",
        "kind": "dispersion",
        "file": BENCH_README,
        "artifact": BENCH,
        "list": "results",
        "group_by": "pass_idx",
        "metric": "llm_calls",
        "arm": "baseline",
        "field": "stderr",
        "regex": r"baseline LLM calls [\d.]+ / [\d.]+ / [\d.]+ \(stderr ([\d.]+)\)",
    },
    {
        "label": "hive per-pass LLM-call stderr (README)",
        "kind": "dispersion",
        "file": BENCH_README,
        "artifact": BENCH,
        "list": "results",
        "group_by": "pass_idx",
        "metric": "llm_calls",
        "arm": "hive",
        "field": "stderr",
        "regex": r"Hive [\d.]+ / [\d.]+ / [\d.]+ \(stderr ([\d.]+)\)",
    },
    # ---- restated copies: the same numbers outside README.md (ungated until now) ----
    {
        "label": "bake-off mlp next-tool accuracy (benchmarks/README.md)",
        "kind": "single",
        "file": "benchmarks/README.md",
        "artifact": BAKEOFF,
        "path": "models[mlp].argmax_overall.agreement",
        "percent": True,
        "regex": r"^\| mlp \| ([\d.]+)% \|",
    },
    {
        "label": "bake-off repeat-last baseline (benchmarks/README.md)",
        "kind": "single",
        "file": "benchmarks/README.md",
        "artifact": BAKEOFF,
        "path": "models[repeat-last].argmax_overall.agreement",
        "percent": True,
        "regex": r"^\| \*repeat-last\* \| \*([\d.]+)%\* \|",
    },
    {
        "label": "bake-off majority baseline (benchmarks/README.md)",
        "kind": "single",
        "file": "benchmarks/README.md",
        "artifact": BAKEOFF,
        "path": "models[majority].argmax_overall.agreement",
        "percent": True,
        "regex": r"^\| \*majority\* \| \*([\d.]+)%\* \|",
    },
    {
        "label": "long-context max_ratio (docs/WHATS_NEW.md, every copy)",
        "kind": "single",
        "file": "docs/WHATS_NEW.md",
        "artifact": SMOKE,
        "path": "max_ratio",
        "regex": r"([\d.]+)×",  # noqa: RUF001 — matches the README's ×
    },
    # ---- A6: trace bake-off accuracy ----
    {
        "label": "A6 mlp next-tool accuracy",
        "kind": "single",
        "artifact": BAKEOFF,
        "path": "models[mlp].argmax_overall.agreement",
        "percent": True,
        "regex": r"\*\*([\d.]+)%\*\* raw next-tool accuracy",
    },
    {
        "label": "A6 repeat-last baseline",
        "kind": "single",
        "artifact": BAKEOFF,
        "path": "models[repeat-last].argmax_overall.agreement",
        "percent": True,
        "regex": r"vs ([\d.]+)% repeat-last",
    },
    {
        "label": "A6 majority baseline",
        "kind": "single",
        "artifact": BAKEOFF,
        "path": "models[majority].argmax_overall.agreement",
        "percent": True,
        "regex": r"and ([\d.]+)% majority",
    },
    # ---- capability bench: held-out tasks, 3 arms, verdicts ----
    {
        "label": "capability baseline resolve rate",
        "kind": "multi",
        "artifact": CAPABILITY,
        "paths": [{"path": "summary.baseline.resolve_rate", "percent": True},
                  "summary.baseline.resolved", "summary.baseline.tasks"],
        "abs_tol": 0.5,
        "regex": r"^\| baseline \| \*\*([\d.]+)%\*\* \((\d+)/(\d+)\)",
    },
    {
        "label": "capability context resolve rate",
        "kind": "multi",
        "artifact": CAPABILITY,
        "paths": [{"path": "summary.context.resolve_rate", "percent": True},
                  "summary.context.resolved", "summary.context.tasks"],
        "abs_tol": 0.5,
        "regex": r"^\| context \| \*\*([\d.]+)%\*\* \((\d+)/(\d+)\)",
    },
    {
        "label": "capability hive resolve rate",
        "kind": "multi",
        "artifact": CAPABILITY,
        "paths": [{"path": "summary.hive.resolve_rate", "percent": True},
                  "summary.hive.resolved", "summary.hive.tasks"],
        "abs_tol": 0.5,
        "regex": r"^\| hive \| \*\*([\d.]+)%\*\* \((\d+)/(\d+)\)",
    },
    {
        "label": "capability baseline pass^5",
        "kind": "single",
        "artifact": CAPABILITY,
        "path": "summary.baseline.pass_hat_k[5].value",
        "percent": True,
        "abs_tol": 0.5,
        "regex": r"baseline \| .*? \| \*\*([\d.]+)%\*\* pass\^5",
    },
    {
        "label": "capability hive pass^5",
        "kind": "single",
        "artifact": CAPABILITY,
        "path": "summary.hive.pass_hat_k[5].value",
        "percent": True,
        "abs_tol": 0.5,
        "regex": r"hive \| .*? \| \*\*([\d.]+)%\*\* pass\^5",
    },
    {
        "label": "capability baseline usd/resolved",
        "kind": "single",
        "artifact": CAPABILITY,
        "path": "summary.baseline.usd_per_resolved_task",
        "abs_tol": 1e-4,
        "regex": r"baseline \| .*? \| .*? \| \$([\d.]+)/resolved",
    },
    {
        "label": "capability hive usd/resolved",
        "kind": "single",
        "artifact": CAPABILITY,
        "path": "summary.hive.usd_per_resolved_task",
        "abs_tol": 1e-4,
        "regex": r"hive \| .*? \| .*? \| \$([\d.]+)/resolved",
    },
    {
        "label": "capability context usd/resolved",
        "kind": "single",
        "artifact": CAPABILITY,
        "path": "summary.context.usd_per_resolved_task",
        "abs_tol": 1e-4,
        "regex": r"context \| .*? \| .*? \| \$([\d.]+)/resolved",
    },
    {
        "label": "capability baseline_vs_hive verdict",
        "kind": "verbatim",
        "artifact": CAPABILITY,
        "path": "summary.comparisons.baseline_vs_hive.verdict",
        "regex": r"baseline vs hive: \*\*(\w+)\*\*",
    },
    {
        "label": "capability baseline_vs_hive McNemar p",
        "kind": "comparison",
        "artifact": CAPABILITY,
        "pair": "baseline_vs_hive",
        "path": "summary.comparisons.baseline_vs_hive.p",
        "regex": r"baseline vs hive: \*\*\w+\*\* \(p=([\d.]+)[^)]*\)",
        "abs_tol": 1e-4,
    },
    {
        "label": "capability context_vs_hive verdict",
        "kind": "verbatim",
        "artifact": CAPABILITY,
        "path": "summary.comparisons.context_vs_hive.verdict",
        "regex": r"context vs hive: \*\*(\w+)\*\*",
    },
    {
        "label": "capability context_vs_hive McNemar p",
        "kind": "comparison",
        "artifact": CAPABILITY,
        "pair": "context_vs_hive",
        "path": "summary.comparisons.context_vs_hive.p",
        "abs_tol": 1e-4,
        "regex": r"context vs hive: \*\*\w+\*\* \(p=([\d.]+)\)",
    },
    {
        "label": "capability baseline_vs_context verdict",
        "kind": "verbatim",
        "artifact": CAPABILITY,
        "path": "summary.comparisons.baseline_vs_context.verdict",
        "regex": r"baseline vs context: \*\*(\w+)\*\*",
    },
    {
        "label": "capability baseline_vs_context McNemar p",
        "kind": "comparison",
        "artifact": CAPABILITY,
        "pair": "baseline_vs_context",
        "path": "summary.comparisons.baseline_vs_context.p",
        "abs_tol": 1e-4,
        "regex": r"baseline vs context: \*\*\w+\*\* \(p=([\d.]+)\)",
    },
    # ---- hard tier: the README + RELEASE_NOTES headline table (numerators, denominators, percentages) ----
    {
        "label": "hard resolve counts (README + RELEASE_NOTES headline)",
        "kind": "multi",
        "file": ["README.md", "docs/RELEASE_NOTES.md"],
        "artifact": HARD,
        "paths": ["summary.baseline.resolved", "summary.baseline.tasks",
                  {"path": "summary.baseline.resolve_rate", "percent": True},
                  "summary.context.resolved", "summary.context.tasks",
                  {"path": "summary.context.resolve_rate", "percent": True},
                  "summary.hive.resolved", "summary.hive.tasks",
                  {"path": "summary.hive.resolve_rate", "percent": True}],
        "abs_tol": 0.5,
        "regex": r"^\| \*\*Resolve rate\*\* \| (\d+)/(\d+) \((\d+)%\) \| (\d+)/(\d+) \((\d+)%\) \| \*\*(\d+)/(\d+) \((\d+)%\)\*\*",
    },
    {
        "label": "hard mean LLM calls (README + RELEASE_NOTES headline)",
        "kind": "multi",
        "file": ["README.md", "docs/RELEASE_NOTES.md"],
        "artifact": HARD,
        "paths": ["summary.baseline.mean_llm_calls",
                  "summary.context.mean_llm_calls", "summary.hive.mean_llm_calls"],
        "regex": r"^\| \*\*Mean LLM calls\*\* \| ([\d.]+) \| ([\d.]+) \| \*\*([\d.]+)\*\*",
    },
    {
        "label": "hard usd totals (README + RELEASE_NOTES headline)",
        "kind": "multi",
        "file": ["README.md", "docs/RELEASE_NOTES.md"],
        "artifact": HARD,
        "paths": ["summary.baseline.usd_total", "summary.context.usd_total",
                  "summary.hive.usd_total"],
        "abs_tol": 2e-3,
        "regex": r"^\| \*\*Total cost \(est\.\)\*\* \| \$([\d.]+) \| \$([\d.]+) \| \*\*\$([\d.]+)\*\*",
    },
    {
        "label": "hard McNemar context cell (README + RELEASE_NOTES)",
        "kind": "comparison",
        "file": ["README.md", "docs/RELEASE_NOTES.md"],
        "artifact": HARD,
        "pair": "baseline_vs_context",
        "p_group": 1,
        "verdict_group": 0,
        "verdict_path": "summary.comparisons.baseline_vs_context.verdict",
        "abs_tol": 1e-4,
        "regex": r"^\| \*\*McNemar vs baseline\*\* \| — \| (\w+) \(p=([\d.]+)\)",
    },
    {
        "label": "hard McNemar hive cell (README + RELEASE_NOTES)",
        "kind": "comparison",
        "file": ["README.md", "docs/RELEASE_NOTES.md"],
        "artifact": HARD,
        "pair": "baseline_vs_hive",
        "p_group": 1,
        "verdict_group": 0,
        "verdict_path": "summary.comparisons.baseline_vs_hive.verdict",
        "abs_tol": 1e-4,
        "regex": r"\| \*\*(\w+) \(p=([\d.]+)\)\*\* \|$",
    },
    # benchmarks/README hard tier: every per-task row, the total row, every arm row
    *[
        {
            "label": f"hard per-task row: {task}",
            "kind": "task_row",
            "file": [BENCH_README, "docs/benchmarks/PROVENANCE.md"],
            "artifact": HARD,
            "task": task,
            "arms": ["baseline", "context", "hive"],
            "regex": rf"^\| {re.escape(task)} \| (\d+)/(\d+) \| (\d+)/(\d+) \| (\d+)/(\d+) \|",
        }
        for task in ("sliding-window-limit", "snapshot-event-fold",
                     "kway-merge-dedup", "reservation-expiry",
                     "idempotent-outbox", "lru-ttl-cache")
    ],
    {
        "label": "hard total row (benchmarks/README + PROVENANCE)",
        "kind": "multi",
        "file": [BENCH_README, "docs/benchmarks/PROVENANCE.md"],
        "artifact": HARD,
        "paths": ["summary.baseline.resolved", "summary.baseline.tasks",
                  {"path": "summary.baseline.resolve_rate", "percent": True},
                  "summary.context.resolved", "summary.context.tasks",
                  {"path": "summary.context.resolve_rate", "percent": True},
                  "summary.hive.resolved", "summary.hive.tasks",
                  {"path": "summary.hive.resolve_rate", "percent": True}],
        "abs_tol": 0.5,
        "regex": r"^\| \*\*Total\*\* \| \*\*(\d+)/(\d+) \((\d+)%\)\*\* \| \*\*(\d+)/(\d+) \((\d+)%\)\*\* \| \*\*(\d+)/(\d+) \((\d+)%\)\*\*",
    },
    *[
        {
            "label": f"hard {arm} cost row (benchmarks/README)",
            "kind": "multi",
            "file": BENCH_README,
            "artifact": HARD,
            "paths": [f"summary.{arm}.mean_llm_calls", f"summary.{arm}.usd_total",
                      f"summary.{arm}.usd_per_resolved_task"],
            "abs_tol": 2e-3,
            "regex": rf"^\| {arm} \| \*?\*?([\d.]+)\*?\*? \| \*?\*?\$([\d.]+)\*?\*? \| \*?\*?\$([\d.]+)",
        }
        for arm in ("baseline", "context", "hive")
    ],
    # ---- two-task ceiling annex (reported separately, still gated) ----
    {
        "label": "new2 baseline row (benchmarks/README)",
        "kind": "multi",
        "file": BENCH_README,
        "artifact": NEW2,
        "paths": ["summary.baseline.resolved", "summary.baseline.tasks",
                  {"path": "summary.baseline.resolve_rate", "percent": True, "abs_tol": 0.5},
                  {"path": "summary.baseline.mean_llm_calls", "abs_tol": 2e-3},
                  {"path": "summary.baseline.usd_total", "abs_tol": 2e-3}],
        "abs_tol": 0.5,
        "regex": r"^\| baseline \| (\d+)/(\d+) \((\d+)%\) \| ([\d.]+) \| \$([\d.]+) \|",
    },
    {
        "label": "new2 context row (benchmarks/README)",
        "kind": "multi",
        "file": BENCH_README,
        "artifact": NEW2,
        "paths": ["summary.context.resolved", "summary.context.tasks",
                  {"path": "summary.context.resolve_rate", "percent": True, "abs_tol": 0.5},
                  {"path": "summary.context.mean_llm_calls", "abs_tol": 2e-3},
                  {"path": "summary.context.usd_total", "abs_tol": 2e-3}],
        "abs_tol": 0.5,
        "regex": r"^\| context \| (\d+)/(\d+) \((\d+)%\) \| ([\d.]+) \| \$([\d.]+) \|",
    },
    {
        "label": "new2 hive row (benchmarks/README)",
        "kind": "multi",
        "file": BENCH_README,
        "artifact": NEW2,
        "paths": ["summary.hive.resolved", "summary.hive.tasks",
                  {"path": "summary.hive.resolve_rate", "percent": True, "abs_tol": 0.5},
                  {"path": "summary.hive.mean_llm_calls", "abs_tol": 2e-3},
                  {"path": "summary.hive.usd_total", "abs_tol": 2e-3}],
        "abs_tol": 0.5,
        "regex": r"^\| hive \(trained\) \| (\d+)/(\d+) \((\d+)%\) \| \*\*([\d.]+)\*\* \| \*\*\$([\d.]+)\*\* \|",
    },
    # ---- hard tier with the TRAINED policy (checkpoint provenance unverifiable) ----
    {
        "label": "trained resolve count",
        "kind": "multi",
        "file": BENCH_README,
        "artifact": TRAINED,
        "paths": ["summary.hive.resolved", "summary.hive.tasks"],
        "abs_tol": 0.5,
        "regex": r"trained policy[^|]*\| *\*?(\d+)/(\d+)",
    },
    {
        "label": "trained mean LLM calls",
        "kind": "multi",
        "file": BENCH_README,
        "artifact": TRAINED,
        "paths": ["summary.hive.tasks",
                  {"path": "summary.hive.mean_llm_calls", "abs_tol": 2e-3}],
        "abs_tol": 0.5,
        "regex": r"trained policy[^|]*\| *\d+/(\d+)[^|]*\| *\*?([\d.]+)",
    },
    {
        "label": "trained usd total",
        "kind": "multi",
        "file": BENCH_README,
        "artifact": TRAINED,
        "paths": ["summary.hive.tasks",
                  {"path": "summary.hive.usd_total", "abs_tol": 2e-3}],
        "abs_tol": 0.5,
        "regex": r"trained policy[^|]*\| *\d+/(\d+)[^|]*\| *\*?[\d.]+[^|]*\| *\*?\$([\d.]+)",
    },
]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def resolve_raw(root: object, path: str) -> object:
    """Traverse ``a.b`` / ``a[0]`` / ``components[name]`` paths, value untyped."""
    cur = root
    for part in path.split("."):
        m = re.match(r"^([^\[\]]+)(?:\[(.+)\])?$", part)
        if not m:
            raise KeyError(path)
        key, sel = m.group(1), m.group(2)
        cur = cur[key]  # type: ignore[index]
        if sel is None:
            continue
        if isinstance(cur, dict) and sel in cur:
            cur = cur[sel]
        elif sel.isdigit() and isinstance(cur, list):
            cur = cur[int(sel)]  # type: ignore[index]
        else:
            # list-of-dicts containers (e.g. latest-micro.json components) match on "name"
            matches = [c for c in cur if isinstance(c, dict) and c.get("name") == sel]  # type: ignore[union-attr]
            if len(matches) != 1:
                raise KeyError(f"selector {sel!r} in {path!r} matched {len(matches)} entries")
            cur = matches[0]
    return cur


def resolve(root: object, path: str) -> float:
    """Traverse a path and read the number at the end of it."""
    value = resolve_raw(root, path)
    if value is None:
        raise ValueError(f"{path!r} is null — the artifact marks it not measured")
    return float(value)  # type: ignore[arg-type]


def load_artifact(path: str) -> object:
    file = ROOT / path
    if not file.exists():
        raise FileNotFoundError(f"{path} is not committed")
    return json.loads(file.read_text())


def num(text: str) -> float:
    return float(text.replace(",", ""))


def close(readme_value: float, artifact_value: float, rel_tol: float = REL_TOL,
          abs_tol: float = ABS_TOL) -> bool:
    return abs(readme_value - artifact_value) <= max(rel_tol * abs(artifact_value), abs_tol)


def mcnemar_exact(a: list[bool], b: list[bool]) -> dict[str, int | float]:
    """Exact two-sided McNemar over paired outcomes.

    Deliberately reimplemented here rather than imported from the harness that
    wrote the artifact: a claim gate that shares code with the producer checks
    nothing. Stdlib only.
    """
    if len(a) != len(b):
        raise ValueError("paired test needs equal-length outcome lists")
    both = sum(1 for x, y in zip(a, b, strict=True) if x and y)
    a_only = sum(1 for x, y in zip(a, b, strict=True) if x and not y)
    b_only = sum(1 for x, y in zip(a, b, strict=True) if y and not x)
    n = a_only + b_only
    if n == 0:
        p = 1.0
    else:
        k = min(a_only, b_only)
        p = min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)
    return {"both": both, "a_only": a_only, "b_only": b_only, "p": round(p, 6)}


def paired_grid(artifact: dict, pair: str) -> tuple[list[bool], list[bool], int]:
    """Recompute the paired (per-task) outcome vectors for ``"a_vs_b"``."""
    arm_a, _, arm_b = pair.partition("_vs_")
    if not arm_a or not arm_b:
        raise ValueError(f"pair {pair!r} is not in 'a_vs_b' form")
    grid: dict[str, dict[str, list[bool]]] = {arm_a: {}, arm_b: {}}
    for row in artifact["results"]:
        if row["arm"] in grid:
            grid[row["arm"]].setdefault(row["task_id"], []).append(bool(row["resolved"]))
    shared = sorted(set(grid[arm_a]) & set(grid[arm_b]))
    if not shared:
        raise ValueError(f"no task ran in both arms of {pair!r}")
    def majority(arm: str) -> list[bool]:
        return [sum(grid[arm][t]) * 2 > len(grid[arm][t]) for t in shared]
    return majority(arm_a), majority(arm_b), len(shared)



def run_check(check: dict, texts: dict[str, str]) -> list[str]:
    """Return a list of problem strings (empty = pass); prints OK/MISMATCH inline.

    A check may name one file or several. Every listed file must contain at least
    one match, and *every* match in *every* listed file must agree with the
    artifact — a number restated in three places is checked in all three.
    """
    label = check["label"]
    files = check.get("file", BENCH_README)
    files = [files] if isinstance(files, str) else list(files)
    pattern = re.compile(check["regex"], re.MULTILINE)

    all_hits: list[tuple[str, tuple]] = []
    problems: list[str] = []
    for name in files:
        text = texts.get(name)
        if text is None:
            problems.append(f"MISSING\t{label}\t{name} not found")
            continue
        file_hits = pattern.findall(text)
        if not file_hits:
            problems.append(f"MISSING\t{label}\tpattern matched 0 times in {name}")
            continue
        for h in file_hits:
            all_hits.append((name, h if isinstance(h, tuple) else (h,)))
    if problems:
        return problems
    artifact = load_artifact(check["artifact"])
    problems = []

    if check["kind"] == "pair":
        art_baseline = resolve(artifact, check["baseline"])
        art_hive = resolve(artifact, check["hive"])
        art_delta = (art_hive - art_baseline) / art_baseline * 100
        rel_tol = check.get("rel_tol", REL_TOL)
        abs_tol = check.get("abs_tol", ABS_TOL)
        for where, g in all_hits:
            readme_baseline, readme_hive = num(str(g[0])), num(str(g[1]))
            for part, rv, av in (
                ("baseline", readme_baseline, art_baseline),
                ("hive", readme_hive, art_hive),
            ):
                if not close(rv, av, rel_tol, abs_tol):
                    problems.append(f"MISMATCH\t{label} [{part}]\t{where}: readme={rv} artifact={av}")
            if "delta_sign" in check:
                readme_delta = num(str(g[2])) * check["delta_sign"]
                if not close(readme_delta, art_delta):
                    problems.append(
                        f"MISMATCH\t{label} [delta]\t{where}: readme={readme_delta} artifact={round(art_delta, 2)}"
                    )
        if not problems:
            print(f"OK\t{label}\t{len(all_hits)} occurrence(s)\tartifact={art_baseline}/{art_hive}")
        return problems

    if check["kind"] == "dispersion":
        rows = [r for r in artifact[check["list"]] if r["arm"] == check["arm"]]
        passes = sorted({r[check["group_by"]] for r in rows})
        per_pass = [
            sum(r[check["metric"]] for r in rows if r[check["group_by"]] == p)
            / len([r for r in rows if r[check["group_by"]] == p])
            for p in passes
        ]
        if len(per_pass) < 2:
            return [f"MISSING\t{label}\tartifact has {len(per_pass)} pass(es); no dispersion to check"]
        mean = sum(per_pass) / len(per_pass)
        var = sum((v - mean) ** 2 for v in per_pass) / (len(per_pass) - 1)
        artifact_value = var**0.5 / len(per_pass) ** 0.5  # sample stderr of the mean
        for where, g in all_hits:
            readme_value = num(str(g[0]))
            # published to 2 decimals: compare at that precision
            if round(readme_value, 2) != round(artifact_value, 2):
                problems.append(
                    f"MISMATCH\t{label}\t{where}: readme={readme_value} "
                    f"artifact={round(artifact_value, 4)} (per-pass {[round(v, 2) for v in per_pass]})"
                )
        if not problems:
            print(f"OK\t{label}\tstderr over {len(per_pass)} passes\tartifact={round(artifact_value, 4)}")
        return problems

    if check["kind"] == "group_mean":
        rows = artifact[check["list"]]
        group = [r for r in rows if r.get(check["group_by"]) == check["group"]]
        if not group:
            return [f"MISSING\t{label}\tno rows with {check['group_by']}={check['group']} in {check['artifact']}"]
        artifact_value = sum(r[check["metric"]] for r in group) / len(group)
        for where, g in all_hits:
            readme_value = num(str(g[0]))
            if not close(readme_value, artifact_value, check.get("rel_tol", REL_TOL)):
                problems.append(
                    f"MISMATCH\t{label}\t{where}: readme={readme_value} "
                    f"artifact={round(artifact_value, 4)} (mean of {len(group)} rows)"
                )
        if not problems:
            print(f"OK\t{label}\tmean of {len(group)} rows\tartifact={round(artifact_value, 4)}")
        return problems

    if check["kind"] == "comparison":
        # Recompute the paired test from the artifact's own per-task grid and
        # demand it match both the artifact's own summary and the README.
        a, b, n_tasks = paired_grid(artifact, check["pair"])
        recomputed = mcnemar_exact(a, b)
        if "path" in check:
            published = resolve(artifact, check["path"])
            if not close(recomputed["p"], float(published),
                         check.get("rel_tol", REL_TOL), check.get("abs_tol", ABS_TOL)):
                problems.append(
                    f"MISMATCH\t{label}\tartifact summary says p={published} but the "
                    f"per-task grid in {check['artifact']} gives p={recomputed['p']} "
                    f"(tasks={n_tasks} a_only={recomputed['a_only']} b_only={recomputed['b_only']})"
                )
        p_idx = check.get("p_group", 0)
        verdict_idx = check.get("verdict_group")
        expected_verdict = (str(resolve_raw(artifact, check["verdict_path"]))
                            if "verdict_path" in check else None)
        for where, g in all_hits:
            if not close(num(str(g[p_idx])), recomputed["p"],
                         check.get("rel_tol", REL_TOL), check.get("abs_tol", ABS_TOL)):
                problems.append(
                    f"MISMATCH\t{label}\t{where}: readme={num(str(g[p_idx]))} "
                    f"recomputed={recomputed['p']}"
                )
            if expected_verdict is not None and str(g[verdict_idx]) != expected_verdict:
                problems.append(
                    f"MISMATCH\t{label}\t{where}: readme verdict={g[verdict_idx]!r} "
                    f"artifact verdict={expected_verdict!r}"
                )
        if not problems:
            print(f"OK\t{label}\trecomputed from {n_tasks} paired tasks\t"
                  f"artifact={recomputed['p']}")
        return problems

    if check["kind"] == "multi":
        # One regex captures several cells; each capture is compared to its own
        # artifact path (positional, in order). A spec may be a plain path
        # string or a dict {"path": ..., "percent": true, "abs_tol": ...} —
        # percent multiplies the artifact value by 100 for displayed
        # percentages, and abs_tol overrides the check-level tolerance so
        # counts/percentages and dollar amounts can share one row.
        specs = check["paths"]
        rel_tol = check.get("rel_tol", REL_TOL)
        abs_tol = check.get("abs_tol", ABS_TOL)
        expected: list[tuple[str, float, float, float]] = []
        for spec in specs:
            if isinstance(spec, str):
                expected.append((spec, resolve(artifact, spec), rel_tol, abs_tol))
            else:
                value = resolve(artifact, spec["path"])
                if spec.get("percent"):
                    value *= 100
                expected.append((spec["path"], value,
                                 spec.get("rel_tol", rel_tol),
                                 spec.get("abs_tol", abs_tol)))
        for where, g in all_hits:
            if len(g) != len(specs):
                problems.append(
                    f"MISSING\t{label}\t{where}: regex captured {len(g)} groups, "
                    f"expected {len(specs)}")
                continue
            for (path, av, rt, at), rv in zip(expected, g, strict=True):
                readme_value = num(str(rv))
                if not close(readme_value, av, rt, at):
                    problems.append(
                        f"MISMATCH\t{label} [{path}]\t{where}: "
                        f"readme={readme_value} artifact={round(av, 4)}")
        if not problems:
            print(f"OK\t{label}\t{len(all_hits)} occurrence(s)\t"
                  f"{len(specs)} cells each")
        return problems

    if check["kind"] == "task_row":
        # A per-task table row: each arm's "resolved/total" cell must equal the
        # artifact's per_task_resolved entry for that task and arm.
        arms = check["arms"]
        expected: list[tuple[int, int]] = []
        for arm in arms:
            cell = str(resolve_raw(artifact, f"summary.{arm}.per_task_resolved.{check['task']}"))
            m = re.match(r"^(\d+)/(\d+)$", cell)
            if not m:
                return [f"MISSING\t{label}\tartifact cell {cell!r} is not 'n/m'"]
            expected.append((int(m.group(1)), int(m.group(2))))
        for where, g in all_hits:
            if len(g) != 2 * len(arms):
                problems.append(
                    f"MISSING\t{label}\t{where}: regex captured {len(g)} groups, "
                    f"expected {2 * len(arms)}")
                continue
            for arm, (num_s, den_s), (exp_n, exp_d) in zip(
                    arms, zip(g[::2], g[1::2], strict=True), expected, strict=True):
                if int(num_s) != exp_n or int(den_s) != exp_d:
                    problems.append(
                        f"MISMATCH\t{label} [{arm}]\t{where}: "
                        f"readme={num_s}/{den_s} artifact={exp_n}/{exp_d}")
        if not problems:
            print(f"OK\t{label}\t{len(all_hits)} occurrence(s)\t{len(arms)} arm cells")
        return problems

    if check["kind"] == "null_expected":
        # The claim is that the artifact publishes null (not measured) and the
        # README shows a dash — both halves are asserted.
        value = resolve_raw(artifact, check["path"])
        if value is not None:
            problems.append(f"MISMATCH\t{label}\tartifact has {value!r}, expected null")
        for where, g in all_hits:
            if str(g[0]).strip() not in ("—", "-", "n/a", "null"):
                problems.append(f"MISMATCH\t{label}\t{where}: readme={g[0]!r}, expected a dash")
        if not problems:
            print(f"OK\t{label}\tnull in artifact, dash in README")
        return problems

    if check["kind"] == "verbatim":
        # A claim that is a word, not a number (e.g. the verdict): the README
        # must state exactly what the artifact computed.
        value = str(resolve_raw(artifact, check["path"]))
        for name in files:
            if value not in texts[name]:
                problems.append(f"MISSING\t{label}\t{name} does not state {value!r}")
        if not problems:
            print(f"OK\t{label}\tverbatim\tartifact={value!r}")
        return problems

    artifact_value = resolve(artifact, check["path"])
    if check.get("percent"):
        artifact_value *= 100
    rel_tol = check.get("rel_tol", REL_TOL)
    abs_tol = check.get("abs_tol", ABS_TOL)
    for where, g in all_hits:
        readme_value = num(str(g[0]))
        if not close(readme_value, artifact_value, rel_tol, abs_tol):
            problems.append(f"MISMATCH\t{label}\t{where}: readme={readme_value} artifact={round(artifact_value, 4)}")
    if not problems:
        print(f"OK\t{label}\t{len(all_hits)} occurrence(s)\tartifact={round(artifact_value, 4)}")
    return problems


def main() -> int:
    if not README_PATH.exists():
        print(f"MISSING\tREADME.md not found at {README_PATH}")
        return 1
    wanted: set[str] = set()
    for check in CHECKS:
        files = check.get("file", BENCH_README)
        wanted.update([files] if isinstance(files, str) else files)
    texts: dict[str, str] = {}
    for name in sorted(wanted):
        path = ROOT / name
        if path.exists():
            texts[name] = path.read_text()
    problems: list[str] = []
    for check in CHECKS:
        try:
            problems.extend(run_check(check, texts))
        except Exception as exc:  # artifact missing, path traversal failure, ...
            problems.append(f"MISSING\t{check['label']}\t{type(exc).__name__}: {exc}")

    print(f"\n{len(CHECKS) - len(problems)}/{len(CHECKS)} claim checks OK")
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
