"""Tests for LinUCB policy updater."""

from __future__ import annotations

from hive.feedback import OutcomeType, RoutingOutcome
from hive.policy_updater import LinUCBPolicy, PolicyUpdater


def test_linucb_predict_returns_action():
    policy = LinUCBPolicy()
    result = policy.predict({"goal": "read the config file", "step": 1})
    assert "tool" in result
    assert result["tool"] in policy.actions


def test_linucb_train_updates_from_feedback():
    policy = LinUCBPolicy()
    updater = PolicyUpdater()
    outcomes = [
        RoutingOutcome(
            state={"goal": "read file"},
            routed_action="read_file",
            actual_action="read_file",
            outcome_type=OutcomeType.CORRECT,
        ),
        RoutingOutcome(
            state={"goal": "run tests"},
            routed_action="run_tests",
            actual_action="run_tests",
            outcome_type=OutcomeType.CORRECT,
        ),
    ]
    assert updater.update(policy, outcomes) is True


def test_linucb_prefers_rewarded_action():
    policy = LinUCBPolicy(actions=["read_file", "apply_patch"])
    state = {"goal": "fix the bug in parser"}
    for _ in range(8):
        policy.train(
            [
                {"state": state, "action": "apply_patch", "reward": 1.0},
                {"state": state, "action": "read_file", "reward": 0.0},
            ]
        )
    result = policy.predict(state)
    assert result["tool"] == "apply_patch"
