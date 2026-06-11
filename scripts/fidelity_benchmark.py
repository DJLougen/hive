"""Compression-fidelity benchmark: token reduction vs. critical-fact retention.

Throughput numbers say compression is *fast*. This benchmark measures
whether it is *safe*: after compressing a tool message, can a downstream
agent still see the facts it needs to act (failing test names, exception
lines, target signatures, error lines, exit codes)?

For every sample in the corpus (see ``fidelity_corpus.py``) we measure:

* **token reduction** — 1 - compressed_tokens / original_tokens
* **fact retention** — fraction of ground-truth facts whose needle
  substring survives in the compressed content
* **all-facts rate** — fraction of messages where *every* fact survived
  (a proxy for "the agent's next step is not derailed")

and compare against a naive head-truncation baseline given the *same*
token budget per message, which is what most agent frameworks do today.

Usage:

    python3 scripts/fidelity_benchmark.py \
        [--seed 42] [--per-category 40] \
        [--json results/fidelity_rule_fast.json] \
        [--report docs/benchmarks/fidelity.md]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import platform
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fidelity_corpus import CATEGORIES, Sample, build_corpus  # noqa: E402

from hive.rule_fast import _estimate_tokens  # noqa: E402
from hive.stack import HiveStack  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


def _evaluate(content: str, facts: list[Any]) -> tuple[int, int]:
    retained = sum(1 for f in facts if f.needle in content)
    return retained, len(facts)


def run_benchmark(samples: list[Sample]) -> dict[str, Any]:
    stack = HiveStack()  # no honey-comb installed -> rule_fast path
    compressor = type(stack.comb).__name__

    rows: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    for s in samples:
        out = stack.compress(s.role, s.content)
        retained, total = _evaluate(out.content, s.facts)

        # Naive baseline: head-truncate to the same character budget the
        # compressor used. This is the "tool_output[:N]" most frameworks do.
        naive = s.content[: len(out.content)]
        naive_retained, _ = _evaluate(naive, s.facts)

        rows.append(
            {
                "id": s.id,
                "category": s.category,
                "source": s.source,
                "label": out.label,
                "original_tokens": out.original_tokens,
                "compressed_tokens": out.compressed_tokens,
                "naive_tokens": _estimate_tokens(naive),
                "facts_total": total,
                "facts_retained": retained,
                "naive_facts_retained": naive_retained,
            }
        )
    elapsed_s = time.perf_counter() - t0

    return {
        "compressor": compressor,
        "messages": len(samples),
        "elapsed_s": elapsed_s,
        "throughput_msg_per_s": len(samples) / elapsed_s if elapsed_s else 0.0,
        "rows": rows,
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def _agg(subset: list[dict[str, Any]]) -> dict[str, Any]:
        in_tok = sum(r["original_tokens"] for r in subset)
        out_tok = sum(r["compressed_tokens"] for r in subset)
        facts = sum(r["facts_total"] for r in subset)
        kept = sum(r["facts_retained"] for r in subset)
        naive_kept = sum(r["naive_facts_retained"] for r in subset)
        all_ok = sum(1 for r in subset if r["facts_retained"] == r["facts_total"])
        naive_all_ok = sum(
            1 for r in subset if r["naive_facts_retained"] == r["facts_total"]
        )
        n = len(subset)
        return {
            "messages": n,
            "original_tokens": in_tok,
            "compressed_tokens": out_tok,
            "token_reduction_pct": 100.0 * (1 - out_tok / in_tok) if in_tok else 0.0,
            "facts_total": facts,
            "facts_retained": kept,
            "fact_retention_pct": 100.0 * kept / facts if facts else 100.0,
            "all_facts_rate_pct": 100.0 * all_ok / n if n else 100.0,
            "naive_fact_retention_pct": 100.0 * naive_kept / facts if facts else 100.0,
            "naive_all_facts_rate_pct": 100.0 * naive_all_ok / n if n else 100.0,
        }

    by_category = {
        cat: _agg([r for r in rows if r["category"] == cat]) for cat in CATEGORIES
    }
    by_source: dict[str, Any] = {}
    for source in sorted({r["source"] for r in rows}):
        by_source[source] = _agg([r for r in rows if r["source"] == source])
    return {"overall": _agg(rows), "by_category": by_category, "by_source": by_source}


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


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


def render_report(result: dict[str, Any]) -> str:
    agg = result["aggregate"]
    overall = agg["overall"]
    lines = [
        "# Compression Fidelity Benchmark",
        "",
        "**Question:** after compression, can a downstream agent still see the",
        "facts it needs to act? Throughput proves the compressor is fast; this",
        "report measures whether it is *safe*.",
        "",
        f"- Compressor: `{result['compressor']}` (the in-repo rule-based fast path)",
        f"- Corpus: {result['messages']} messages "
        f"(seed={result['seed']}, {result['per_category']}/category + real captured fixtures)",
        f"- Baseline: head-truncation of the raw message to the **same** token budget",
        f"- Machine: {result['machine']}",
        f"- Commit: `{result['commit']}` — {result['timestamp']}",
        "",
        "## Overall",
        "",
        "| Metric | rule_fast | naive truncation (same budget) |",
        "|---|---|---|",
        f"| Token reduction | {overall['token_reduction_pct']:.1f}% | {overall['token_reduction_pct']:.1f}% (matched) |",
        f"| Fact retention | **{overall['fact_retention_pct']:.1f}%** | {overall['naive_fact_retention_pct']:.1f}% |",
        f"| Messages with *all* facts intact | **{overall['all_facts_rate_pct']:.1f}%** | {overall['naive_all_facts_rate_pct']:.1f}% |",
        "",
        f"Throughput on this machine: {result['throughput_msg_per_s']:,.0f} msg/s (single core).",
        "",
        "## By category",
        "",
        "| Category | Msgs | Token reduction | Fact retention | All-facts rate | Naive retention |",
        "|---|---|---|---|---|---|",
    ]
    for cat, a in agg["by_category"].items():
        lines.append(
            f"| {cat} | {a['messages']} | {a['token_reduction_pct']:.1f}% "
            f"| {a['fact_retention_pct']:.1f}% | {a['all_facts_rate_pct']:.1f}% "
            f"| {a['naive_fact_retention_pct']:.1f}% |"
        )
    lines += [
        "",
        "## Synthetic vs. real captured output",
        "",
        "| Source | Msgs | Token reduction | Fact retention | All-facts rate |",
        "|---|---|---|---|---|",
    ]
    for source, a in agg["by_source"].items():
        lines.append(
            f"| {source} | {a['messages']} | {a['token_reduction_pct']:.1f}% "
            f"| {a['fact_retention_pct']:.1f}% | {a['all_facts_rate_pct']:.1f}% |"
        )
    lines += [
        "",
        "## How to read this",
        "",
        "- **Fact retention** is the safety metric. 100% token reduction is",
        "  worthless if the agent can no longer see which test failed.",
        "- **All-facts rate** approximates per-step survival: a single lost",
        "  fact can derail the step that consumes the message.",
        "- Categories where rule_fast beats the naive baseline justify the",
        "  content-aware rules; categories where it loses are concrete,",
        "  measured targets for improvement.",
        "",
        "## Not yet measured",
        "",
        "- End-to-end task success with an LLM in the loop (e.g. SWE-bench",
        "  resolve rate with Hive on vs. off). This benchmark bounds the",
        "  information available to the model; it does not measure what the",
        "  model does with it.",
        "- Routing accuracy of `busybee` (separate repo, not installed here).",
        "",
        "## Reproduce",
        "",
        "```bash",
        "pip install -e \".[dev]\"",
        "python3 scripts/fidelity_benchmark.py",
        "```",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--per-category", type=int, default=40)
    parser.add_argument(
        "--json", type=Path, default=REPO_ROOT / "results" / "fidelity_rule_fast.json"
    )
    parser.add_argument(
        "--report", type=Path, default=REPO_ROOT / "docs" / "benchmarks" / "fidelity.md"
    )
    args = parser.parse_args(argv)

    samples = build_corpus(seed=args.seed, per_category=args.per_category)
    result = run_benchmark(samples)
    result["aggregate"] = aggregate(result["rows"])
    result.update(
        seed=args.seed,
        per_category=args.per_category,
        machine=f"{platform.machine()} / {platform.processor() or 'unknown cpu'} / {platform.system()}",
        commit=_git_commit(),
        timestamp=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    )

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(result, indent=2) + "\n")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_report(result))

    overall = result["aggregate"]["overall"]
    print(f"compressor:        {result['compressor']}")
    print(f"messages:          {result['messages']}")
    print(f"token reduction:   {overall['token_reduction_pct']:.1f}%")
    print(
        f"fact retention:    {overall['fact_retention_pct']:.1f}% "
        f"(naive baseline: {overall['naive_fact_retention_pct']:.1f}%)"
    )
    print(
        f"all-facts rate:    {overall['all_facts_rate_pct']:.1f}% "
        f"(naive baseline: {overall['naive_all_facts_rate_pct']:.1f}%)"
    )
    print(f"throughput:        {result['throughput_msg_per_s']:,.0f} msg/s")
    print(f"json:              {args.json}")
    print(f"report:            {args.report}")
    return result


if __name__ == "__main__":
    main()
