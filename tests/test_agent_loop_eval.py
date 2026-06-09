"""Tests for agent-loop eval (no model required)."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from agent_loop_corpus import EPISODES  # noqa: E402
from agent_loop_eval import _parse_tool, aggregate  # noqa: E402


def test_episodes_have_valid_ground_truth():
    tools = {"read_file", "run_tests", "apply_patch", "escalate"}
    for ep in EPISODES:
        assert ep.goal and ep.steps
        for step in ep.steps:
            assert step.tool_output
            assert step.expected_tool in tools


def test_parse_tool_extracts_name():
    assert _parse_tool("read_file") == "read_file"
    assert _parse_tool("I will call run_tests next.") == "run_tests"
    assert _parse_tool("apply_patch") == "apply_patch"
    assert _parse_tool("escalate") == "escalate"
    assert _parse_tool("I don't know") is None


def test_aggregate_computes_step_accuracy():
    rows = [
        {
            "condition": "raw",
            "steps": 4,
            "correct_steps": 3,
            "episode_resolved": False,
            "total_prompt_tokens": 400,
        },
        {
            "condition": "compressed",
            "steps": 4,
            "correct_steps": 4,
            "episode_resolved": True,
            "total_prompt_tokens": 120,
        },
    ]
    agg = aggregate(rows)
    assert agg["raw"]["step_accuracy_pct"] == 75.0
    assert agg["compressed"]["step_accuracy_pct"] == 100.0
    assert agg["compressed"]["resolve_rate_pct"] == 100.0
