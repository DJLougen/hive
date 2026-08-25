"""Tests for enterprise Pydantic schema validation."""

from __future__ import annotations

import pytest

from hive.schemas import (
    AgentState,
    MemoryNodeIn,
    RouteDecisionOut,
    validate_memory,
    validate_state,
)


def test_validate_state_accepts_good_input():
    state = {"goal": "fix bug", "step": 5, "available_tools": ["read_file"]}
    result = validate_state(state)
    assert result["goal"] == "fix bug"
    assert result["step"] == 5


def test_validate_state_rejects_negative_step():
    state = {"goal": "fix bug", "step": -1}
    # pydantic raises ValueError on constraint violations
    with pytest.raises((ValueError, Exception)):
        validate_state(state)


def test_validate_state_allows_extra_keys():
    state = {"goal": "x", "step": 0, "custom_field": "custom_value"}
    result = validate_state(state)
    assert result["custom_field"] == "custom_value"


def test_validate_memory_accepts_good_input():
    result = validate_memory("key", {"data": 42}, trust=0.8)
    assert result["key"] == "key"
    assert result["trust"] == pytest.approx(0.8)


def test_validate_memory_rejects_bad_trust():
    with pytest.raises((ValueError, Exception)):
        validate_memory("key", {}, trust=1.5)


def test_agent_state_model_creation():
    s = AgentState(goal="ship hive", step=3, available_tools=["compress"])
    assert s.goal == "ship hive"
    assert s.step == 3


def test_route_decision_out_bounds():
    d = RouteDecisionOut(tool="read_file", confidence=0.95, escalated=False)
    assert d.tool == "read_file"


def test_memory_node_in_creation():
    m = MemoryNodeIn(key="bug_42", value={"type": "syntax"}, trust=0.9)
    assert m.key == "bug_42"
    assert m.trust == pytest.approx(0.9)


def test_hive_stack_routes_with_validation():
    from hive import HiveStack

    stack = HiveStack(validate=True)
    # Should not raise
    decision = stack.route({"goal": "test", "available_tools": []})
    assert decision.source == "fallback"


def test_hive_stack_routes_without_validation():
    from hive import HiveStack

    stack = HiveStack(validate=False)
    decision = stack.route({"goal": "test", "step": -5})
    assert decision.source == "fallback"
