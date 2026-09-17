"""Tests for A/B test harness."""

from __future__ import annotations

from hive.ab_test import ABTestHarness
from hive.feedback import OutcomeType


class MockPolicy:
    def predict(self, state):
        return {"tool": "test_tool", "confidence": 0.9}


def test_ab_split_routing():
    ab = ABTestHarness(
        control=MockPolicy(),
        variant=MockPolicy(),
        split=0.5,
    )
    # Run many times to see both arms
    arms = set()
    for _ in range(100):
        d = ab.route({"goal": "test"})
        arms.add(d.source)
    assert "control" in arms
    assert "variant" in arms


def test_record_and_stats():
    ab = ABTestHarness(control=MockPolicy(), variant=MockPolicy())
    d = ab.route({"goal": "test"})
    ab.record_outcome(d, "test_tool", OutcomeType.CORRECT.value)
    stats = ab.stats()
    assert stats["control_samples"] + stats["variant_samples"] == 1


def test_winner_needs_min_samples():
    ab = ABTestHarness(control=MockPolicy(), variant=MockPolicy(), min_samples=10)
    assert not ab.is_winner()


def test_winner_needs_both_arms():
    """Variant-only data can never declare a winner, however good it looks."""
    ab = ABTestHarness(control=MockPolicy(), variant=MockPolicy(), min_samples=5)
    for _ in range(10):
        d = ab.route({"goal": "test"})
        d.source = "variant"  # force arm attribution for the outcome log
        ab.record_outcome(d, "test_tool", OutcomeType.CORRECT.value)
    assert ab.stats()["variant_samples"] == 10
    assert ab.stats()["control_samples"] == 0
    assert not ab.is_winner()


def test_winner_when_both_arms_meet_minimum():
    ab = ABTestHarness(control=MockPolicy(), variant=MockPolicy(), min_samples=5)
    for i in range(10):
        d = ab.route({"goal": "test"})
        d.source = "variant" if i < 5 else "control"
        outcome = OutcomeType.CORRECT.value if i < 5 else "incorrect"
        ab.record_outcome(d, "test_tool", outcome)
    assert ab.is_winner()

def test_promote_and_rollback():
    ab = ABTestHarness(control=MockPolicy(), variant=MockPolicy())
    ab.promote_variant()
    d = ab.route({"goal": "test"})
    assert d.source == "variant"
    ab.rollback()
    d = ab.route({"goal": "test"})
    assert d.source == "control"
