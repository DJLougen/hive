"""Regression tests for the core-runtime review round.

Covers: route() feedback binding under concurrency, update_policy() keeping
outcomes on failure, FeedbackBuffer drop accounting + thread safety,
RustBrain eviction with falsy keys, config validation/posture surfacing,
default_ttl_s read-path expiry, and otel_endpoint wiring.
"""

from __future__ import annotations

import logging
import threading
import time

import pytest

from hive import HiveStack
from hive.config import HiveConfig
from hive.feedback import FeedbackBuffer, OutcomeType, RoutingOutcome
from hive.rule_fast import RuleFastHoneyComb
from hive.rust_brain import RustBrain
from hive.stack import RouteDecision
from hive.telemetry import Telemetry


class _BlockingPolicy:
    """Policy whose predict() blocks until released — lets a second route()
    interleave between the first call's state snapshot and its pending
    append, which is exactly the window where _last_state gets clobbered."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def predict(self, state: dict) -> dict:
        self.entered.set()
        self.release.wait(timeout=5.0)
        return {"tool": "tool_a", "confidence": 0.9}


class _FailingPolicy:
    def predict(self, state: dict) -> dict:
        return {"tool": "tool_a", "confidence": 0.9}

    def train(self, examples) -> bool:
        raise RuntimeError("transient training failure")


def _outcome(goal: str) -> RoutingOutcome:
    return RoutingOutcome(
        state={"goal": goal},
        routed_action="tool_a",
        actual_action="tool_a",
        outcome_type=OutcomeType.CORRECT,
    )


# ---------------------------------------------------------------------------
# 1. route() must bind feedback to the state of its own call
# ---------------------------------------------------------------------------


def test_route_binds_feedback_to_own_state_under_interleave():
    fb = FeedbackBuffer(capacity=10)
    policy = _BlockingPolicy()
    stack = HiveStack(
        honey_comb=RuleFastHoneyComb(), busybee_policy=policy, feedback_buffer=fb
    )

    decision_a: list[RouteDecision] = []
    t = threading.Thread(
        target=lambda: decision_a.append(stack.route({"goal": "a"})), daemon=True
    )
    t.start()
    assert policy.entered.wait(timeout=5.0)
    # While thread A is parked inside predict(), route a second state.
    decision_b = stack.route({"goal": "b"})
    policy.release.set()
    t.join(timeout=5.0)

    stack.record_outcome(decision_a[0], "tool_a", OutcomeType.CORRECT)
    stack.record_outcome(decision_b, "tool_a", OutcomeType.CORRECT)

    states = sorted(o.state.get("goal") for o in fb.get_outcomes())
    assert states == ["a", "b"]


# ---------------------------------------------------------------------------
# 2. update_policy() must not destroy outcomes when the update fails
# ---------------------------------------------------------------------------


def _fill_buffer(stack: HiveStack, fb: FeedbackBuffer, n: int) -> None:
    for i in range(n):
        d = stack.route({"goal": f"g{i}"})
        stack.record_outcome(d, "tool_a", OutcomeType.CORRECT)


def test_update_policy_failure_keeps_outcomes():
    fb = FeedbackBuffer(capacity=10)
    stack = HiveStack(
        honey_comb=RuleFastHoneyComb(),
        busybee_policy=_FailingPolicy(),
        feedback_buffer=fb,
    )
    _fill_buffer(stack, fb, fb.capacity)
    assert stack.should_update_policy()

    assert stack.update_policy() is False
    assert len(fb) == fb.capacity  # outcomes survive a transient failure


def test_update_policy_success_still_clears():
    class _OkPolicy:
        def predict(self, state: dict) -> dict:
            return {"tool": "tool_a", "confidence": 0.9}

        def train(self, examples) -> bool:
            return True

    fb = FeedbackBuffer(capacity=2)
    stack = HiveStack(
        honey_comb=RuleFastHoneyComb(),
        busybee_policy=_OkPolicy(),
        feedback_buffer=fb,
    )
    _fill_buffer(stack, fb, 2)
    assert stack.update_policy() is True
    assert len(fb) == 0


class _SlowOkPolicy:
    def __init__(self) -> None:
        self.release = threading.Event()

    def predict(self, state: dict) -> dict:
        return {"tool": "tool_a", "confidence": 0.9}

    def train(self, examples) -> bool:
        self.release.wait(timeout=5.0)
        return True


def test_update_policy_does_not_drop_outcomes_recorded_during_training():
    """Outcomes added while train() runs must survive a successful update."""
    fb = FeedbackBuffer(capacity=3)
    policy = _SlowOkPolicy()
    stack = HiveStack(
        honey_comb=RuleFastHoneyComb(),
        busybee_policy=policy,
        feedback_buffer=fb,
    )
    _fill_buffer(stack, fb, 3)
    assert stack.should_update_policy()

    done = threading.Event()

    def _run() -> None:
        stack.update_policy()
        done.set()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    time.sleep(0.05)
    d = stack.route({"goal": "during_train"})
    stack.record_outcome(d, "tool_a", OutcomeType.CORRECT)
    policy.release.set()
    assert done.wait(timeout=5.0)

    goals = {o.state.get("goal") for o in fb.get_outcomes()}
    assert goals == {"during_train"}


# ---------------------------------------------------------------------------
# 3. FeedbackBuffer: dropped counter + thread safety
# ---------------------------------------------------------------------------


def test_feedback_buffer_counts_dropped(caplog):
    fb = FeedbackBuffer(capacity=2)
    with caplog.at_level(logging.WARNING, logger="hive.feedback"):
        for i in range(4):
            fb.add(_outcome(f"g{i}"))
    assert len(fb) == 2
    assert fb.stats()["dropped"] == 2
    assert fb.summary()["dropped"] == 2
    assert any("capacity" in r.getMessage() for r in caplog.records)


def test_feedback_buffer_concurrent_records_accounted():
    fb = FeedbackBuffer(capacity=100)
    threads = [
        threading.Thread(
            target=lambda n=n: [fb.add(_outcome(f"{n}-{i}")) for i in range(50)],
            daemon=True,
        )
        for n in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)
    assert len(fb) + fb.stats()["dropped"] == 200


# ---------------------------------------------------------------------------
# 4. RustBrain eviction: falsy keys and order-index consistency
# ---------------------------------------------------------------------------


def test_eviction_terminates_with_empty_string_key():
    brain = RustBrain(tenant_isolation=False, max_nodes=1)
    done = threading.Event()

    def _run() -> None:
        brain.remember("k", 1)
        brain.remember("", 2)
        done.set()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    assert done.wait(timeout=5.0), "eviction spun on a falsy key"
    assert len(brain) == 1
    assert brain.stats()["evictions"] == 1


def test_eviction_keeps_order_index_consistent():
    brain = RustBrain(max_nodes=3)
    for i in range(30):
        brain.remember(f"k{i}", i)
    assert len(brain) == 3
    assert len(brain._order) == len(brain._nodes) == len(brain._order_index)
    found = {n.key for n in brain.search()}
    assert found == {"k27", "k28", "k29"}


def test_snapshot_includes_empty_string_key():
    brain = RustBrain(tenant_isolation=False)
    brain.remember("", "empty-key-value")
    brain.remember("k", 1)
    keys = {n["key"] for n in brain.snapshot()}
    assert keys == {"", "k"}


# ---------------------------------------------------------------------------
# 5. Config validation + zero-config posture
# ---------------------------------------------------------------------------


def test_stack_validates_config_on_init():
    with pytest.raises(ValueError):
        HiveStack(
            honey_comb=RuleFastHoneyComb(), config=HiveConfig(rate_limit=-1)
        )
    with pytest.raises(ValueError):
        HiveStack(
            honey_comb=RuleFastHoneyComb(), config=HiveConfig(max_memory_nodes=0)
        )


def test_stack_warns_once_when_all_controls_off(caplog, monkeypatch):
    # The warning is once per *process* (a library that warns on every default
    # construction trains users to ignore warnings), so reset the sentinel and
    # assert the contract: first stack warns, second does not.
    import hive.stack as stack_mod

    monkeypatch.setattr(stack_mod, "_UNSAFE_POSTURE_WARNED", False)
    with caplog.at_level(logging.WARNING, logger="hive.stack"):
        HiveStack(honey_comb=RuleFastHoneyComb())
        HiveStack(honey_comb=RuleFastHoneyComb())
    warnings = [r for r in caplog.records if "safety controls" in r.getMessage()]
    assert len(warnings) == 1


def test_stats_exposes_controls():
    stack = HiveStack(honey_comb=RuleFastHoneyComb())
    controls = stack.stats()["controls"]
    assert controls == {
        "rate_limiting": False,
        "ttl": False,
        "audit": False,
    }
    stack2 = HiveStack(
        honey_comb=RuleFastHoneyComb(),
        config=HiveConfig(rate_limit=5, default_ttl_s=60.0, audit_enabled=True),
    )
    assert stack2.stats()["controls"] == {
        "rate_limiting": True,
        "ttl": True,
        "audit": True,
    }


# ---------------------------------------------------------------------------
# 6. default_ttl_s must actually expire reads
# ---------------------------------------------------------------------------


def test_stack_recall_respects_default_ttl():
    stack = HiveStack(
        honey_comb=RuleFastHoneyComb(),
        config=HiveConfig(default_ttl_s=0.05),
    )
    stack.remember("temp", "data")
    assert stack.recall("temp") == "data"
    time.sleep(0.1)
    assert stack.recall("temp") is None
    assert stack.brain.get("temp") is None
    assert "temp" not in stack.brain


def test_brain_recall_respects_ttl_without_expire_call():
    brain = RustBrain(default_ttl_s=0.05)
    brain.remember("k", "v")
    time.sleep(0.1)
    assert brain.recall("k") is None
    assert brain.get("k") is None


def test_brain_search_and_snapshot_respect_ttl():
    brain = RustBrain(default_ttl_s=0.05)
    brain.remember("secret", "data", tags=("pii",))
    time.sleep(0.1)
    assert brain.recall("secret") is None
    assert brain.search(tag="pii") == []
    assert brain.snapshot() == []


# ---------------------------------------------------------------------------
# 7. otel_endpoint must reach the telemetry layer
# ---------------------------------------------------------------------------


def test_otel_endpoint_forwarded_to_telemetry(monkeypatch):
    tel = Telemetry()
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        tel,
        "enable_otel_traces",
        lambda endpoint=None: captured.setdefault("endpoint", endpoint),
    )
    HiveStack(
        honey_comb=RuleFastHoneyComb(),
        telemetry=tel,
        config=HiveConfig(otel_endpoint="http://otel:4317"),
    )
    assert captured["endpoint"] == "http://otel:4317"


def test_otel_endpoint_without_extra_raises():
    try:
        import opentelemetry.sdk  # noqa: F401

        pytest.skip("opentelemetry-sdk installed; raise path not reachable")
    except ImportError:
        pass
    with pytest.raises(ImportError):
        HiveStack(
            honey_comb=RuleFastHoneyComb(),
            telemetry=Telemetry(),
            config=HiveConfig(otel_endpoint="http://otel:4317"),
        )
