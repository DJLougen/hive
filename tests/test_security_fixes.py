"""Security review fixes — regression tests for the 5 validated issues.

1. HIGH   — Online-learning policy poisoning (forged feedback rejected)
2. MEDIUM — Prometheus metrics bound to localhost
3. MEDIUM — RustBrain max_nodes eviction
4. MEDIUM — FeedbackBuffer max_state_bytes truncation
5. MEDIUM — compress() max_content_bytes enforcement
"""

from __future__ import annotations

import pytest

from hive import HiveStack
from hive.feedback import FeedbackBuffer, OutcomeType, RoutingOutcome
from hive.rule_fast import RuleFastHoneyComb
from hive.rust_brain import RustBrain

# ---------------------------------------------------------------------------
# 1. HIGH — Policy poisoning
# ---------------------------------------------------------------------------

def test_forged_feedback_is_rejected():
    """A decision that does not match the last route() must be rejected."""
    fb = FeedbackBuffer(capacity=10)
    stack = HiveStack(honey_comb=RuleFastHoneyComb(), feedback_buffer=fb)

    # Make a legitimate route
    d1 = stack.route({"goal": "test", "available_tools": []})
    assert d1.source == "fallback"

    # Try to record a forged decision
    from hive.stack import RouteDecision

    forged = RouteDecision(
        tool="malicious_tool",
        args={},
        confidence=1.0,
        escalated=False,
        source="attacker",
    )
    stack.record_outcome(forged, actual_action="malicious_tool", outcome_type=OutcomeType.CORRECT)

    # Buffer should be empty — forged feedback was rejected
    assert len(fb) == 0


def test_legitimate_feedback_is_accepted():
    """A decision that matches the last route() must be accepted."""
    fb = FeedbackBuffer(capacity=10)
    stack = HiveStack(honey_comb=RuleFastHoneyComb(), feedback_buffer=fb)

    d1 = stack.route({"goal": "test", "available_tools": []})
    stack.record_outcome(d1, actual_action="escalate", outcome_type=OutcomeType.ESCALATED_CORRECTLY)

    assert len(fb) == 1


# ---------------------------------------------------------------------------
# 2. MEDIUM — Prometheus localhost binding
# ---------------------------------------------------------------------------

def test_prometheus_server_logs_localhost():
    """start_prometheus_server should log 127.0.0.1 binding."""
    from hive.telemetry import Telemetry

    tel = Telemetry()
    # We can't actually start the server in tests, but we verify the code
    # path uses addr="127.0.0.1" by inspecting the source
    import inspect

    src = inspect.getsource(tel.start_prometheus_server)
    assert 'addr="127.0.0.1"' in src or "addr='127.0.0.1'" in src


# ---------------------------------------------------------------------------
# 3. MEDIUM — RustBrain max_nodes eviction
# ---------------------------------------------------------------------------

def test_rustbrain_evicts_oldest_when_over_capacity():
    brain = RustBrain(max_nodes=3)
    brain.remember("a", 1)
    brain.remember("b", 2)
    brain.remember("c", 3)
    assert len(brain) == 3

    brain.remember("d", 4)
    # Oldest entry 'a' should have been evicted
    assert len(brain) == 3
    assert brain.recall("a") is None
    assert brain.recall("d") == 4


def test_rustbrain_evicts_oldest_after_forget_and_overflow():
    """Regression: stale _order_index must not evict the wrong live key."""
    brain = RustBrain(max_nodes=2)
    brain.remember("a", 1)
    brain.remember("b", 2)
    brain.remember("c", 3)  # evicts "a"; leaves stale order indices
    brain.forget("b")

    brain.remember("d", 4)
    brain.remember("e", 5)  # must evict "c" (oldest survivor), not "d"

    assert len(brain) == 2
    assert brain.recall("c") is None
    assert brain.recall("d") == 4
    assert brain.recall("e") == 5


def test_rustbrain_forget_keeps_order_index_aligned():
    brain = RustBrain(max_nodes=3)
    for label in ("a", "b", "c", "d"):
        brain.remember(label, label)
    brain.forget("b")

    assert brain._order == ["default:c", "default:d"]
    assert brain._order_index == {"default:c": 0, "default:d": 1}


def test_rustbrain_max_nodes_default_is_10000():
    brain = RustBrain()
    assert brain._max_nodes == 10_000


# ---------------------------------------------------------------------------
# 4. MEDIUM — FeedbackBuffer max_state_bytes truncation
# ---------------------------------------------------------------------------

def test_oversized_state_is_truncated():
    fb = FeedbackBuffer(capacity=10, max_state_bytes=50)
    huge_state = {"data": "x" * 1000}
    outcome = RoutingOutcome(
        state=huge_state,
        routed_action="tool",
        actual_action="tool",
        outcome_type=OutcomeType.CORRECT,
    )
    fb.record(outcome)
    assert len(fb) == 1
    # State should be truncated to empty dict
    assert fb.buffer[0].state == {}


def test_normal_state_is_preserved():
    fb = FeedbackBuffer(capacity=10, max_state_bytes=1024)
    outcome = RoutingOutcome(
        state={"key": "value"},
        routed_action="tool",
        actual_action="tool",
        outcome_type=OutcomeType.CORRECT,
    )
    fb.record(outcome)
    assert fb.buffer[0].state == {"key": "value"}


# ---------------------------------------------------------------------------
# 5. MEDIUM — compress() max_content_bytes
# ---------------------------------------------------------------------------

def test_compress_rejects_oversized_content():
    stack = HiveStack(honey_comb=RuleFastHoneyComb(), max_content_bytes=100)
    with pytest.raises(ValueError, match="exceeds max_content_bytes"):
        stack.compress("user", "x" * 200)


def test_compress_accepts_normal_content():
    stack = HiveStack(honey_comb=RuleFastHoneyComb(), max_content_bytes=1024)
    result = stack.compress("user", "hello world")
    assert result.content == "hello world"


def test_compress_many_rejects_oversized_batch():
    stack = HiveStack(honey_comb=RuleFastHoneyComb(), max_content_bytes=50)
    turns = [("user", "x" * 100)]
    with pytest.raises(ValueError, match="exceeds max_content_bytes"):
        stack.compress_many(turns)
