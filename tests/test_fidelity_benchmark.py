"""Tests for the compression-fidelity benchmark harness."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from fidelity_benchmark import aggregate, run_benchmark  # noqa: E402
from fidelity_corpus import CATEGORIES, build_corpus, load_real_fixtures  # noqa: E402


def _digest(samples) -> str:
    h = hashlib.sha256()
    for s in samples:
        h.update(s.id.encode())
        h.update(s.content.encode())
        for f in s.facts:
            h.update(f.needle.encode())
    return h.hexdigest()


def test_corpus_is_deterministic():
    a = build_corpus(seed=42, per_category=5, include_real=False)
    b = build_corpus(seed=42, per_category=5, include_real=False)
    assert _digest(a) == _digest(b)


def test_corpus_differs_across_seeds():
    a = build_corpus(seed=42, per_category=5, include_real=False)
    b = build_corpus(seed=43, per_category=5, include_real=False)
    assert _digest(a) != _digest(b)


def test_corpus_covers_all_categories_with_facts():
    samples = build_corpus(seed=42, per_category=3, include_real=False)
    seen = {s.category for s in samples}
    assert seen == set(CATEGORIES)
    for s in samples:
        assert s.facts, f"{s.id} has no ground-truth facts"
        for fact in s.facts:
            assert fact.needle in s.content, (
                f"{s.id}: fact {fact.name!r} not present in the original content"
            )


def test_real_fixtures_load_and_are_grounded():
    fixtures = load_real_fixtures()
    assert fixtures, "real fixtures missing; run scripts/capture_real_fixtures.py"
    for s in fixtures:
        assert s.source == "real"
        for fact in s.facts:
            assert fact.needle in s.content


def test_retention_floors_do_not_regress():
    """Pin the measured fidelity floors of rule_fast on the full corpus.

    These values were measured on the default corpus (seed=42, 40/category
    + real fixtures). If a compressor change trips this test, it is
    trading away facts an agent needs — rerun
    ``scripts/fidelity_benchmark.py`` and justify the new numbers.
    """
    samples = build_corpus(seed=42, per_category=40, include_real=True)
    agg = aggregate(run_benchmark(samples)["rows"])

    overall = agg["overall"]
    assert overall["fact_retention_pct"] >= 90.0
    assert overall["all_facts_rate_pct"] >= 75.0
    assert overall["token_reduction_pct"] >= 80.0

    by_cat = agg["by_category"]
    assert by_cat["pytest_log"]["fact_retention_pct"] == 100.0
    assert by_cat["traceback"]["fact_retention_pct"] == 100.0
    assert by_cat["command_output"]["fact_retention_pct"] == 100.0
    assert by_cat["search_results"]["fact_retention_pct"] >= 90.0
    assert by_cat["file_read"]["fact_retention_pct"] >= 50.0

    # The compressor must beat naive truncation at the same token budget.
    assert overall["fact_retention_pct"] > overall["naive_fact_retention_pct"]


def test_benchmark_runs_and_aggregates():
    samples = build_corpus(seed=42, per_category=4, include_real=False)
    result = run_benchmark(samples)
    agg = aggregate(result["rows"])

    overall = agg["overall"]
    assert result["messages"] == len(samples)
    assert 0.0 <= overall["fact_retention_pct"] <= 100.0
    assert 0.0 <= overall["all_facts_rate_pct"] <= 100.0
    assert overall["token_reduction_pct"] > 0.0
    assert set(agg["by_category"]) == set(CATEGORIES)
    # Every row must account for all of its facts.
    for row in result["rows"]:
        assert 0 <= row["facts_retained"] <= row["facts_total"]
        assert 0 <= row["naive_facts_retained"] <= row["facts_total"]
