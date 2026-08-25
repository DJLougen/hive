"""Tests for hive.harness policy loader."""

from __future__ import annotations

from hive.harness import RuleBasedRoutingPolicy, load_routing_policy, policy_label


def test_rule_based_routes_read_file():
    policy = RuleBasedRoutingPolicy()
    result = policy.predict({"goal": "read file src/main.py", "available_tools": ["read_file"]})
    assert result["tool"] == "read_file"
    assert result["escalated"] is False


def test_rule_based_escalates_complex():
    policy = RuleBasedRoutingPolicy()
    result = policy.predict({"goal": "design a new auth architecture", "available_tools": []})
    assert result["tool"] == "escalate"
    assert result["escalated"] is True


def test_load_routing_policy_fallback():
    policy = load_routing_policy()
    assert policy_label(policy) == "rule-based"
