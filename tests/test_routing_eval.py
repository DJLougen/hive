"""Tests for routing eval (requires busybee_cpu)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

busybee = pytest.importorskip("busybee_cpu")

from routing_eval import run_routing_eval  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "routing"


def test_routing_eval_on_bundled_fixtures():
    result = run_routing_eval(
        FIXTURE_DIR / "train_synthetic_200.jsonl",
        FIXTURE_DIR / "eval_synthetic_50.jsonl",
        augment=False,
    )
    h = result["hive_stack"]
    assert result["eval_rows"] == 50
    assert h["action_accuracy_pct"] >= 80.0
    assert h["throughput_routes_per_s"] > 10.0
    assert result["hive_matches_direct"]
