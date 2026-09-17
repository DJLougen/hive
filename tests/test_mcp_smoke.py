"""Tests for MCP server wiring (import smoke)."""

from __future__ import annotations

from hive.mcp_server import HIVE_MCP_TOOLS, main


def test_mcp_module_importable():
    from hive import mcp_server

    assert hasattr(mcp_server, "make_server")
    assert len(HIVE_MCP_TOOLS) == 5


def test_hive_mcp_package_runnable():
    from hive.mcp.__main__ import main as mcp_main

    assert mcp_main(["--help"]) == 0


def test_mcp_main_requires_mcp_package(monkeypatch):
    monkeypatch.setattr("hive.mcp_server._HAS_MCP", False)
    assert main([]) == 1


def test_build_stack_routes_with_default_policy():
    """The default stack must route mechanically, not fall back to escalate."""
    from hive.mcp_server import build_stack

    stack = build_stack()
    decision = stack.route({"goal": "run tests for the repo", "available_tools": []})
    assert decision.source == "busybee"
    assert decision.tool == "run_tests"
    assert not decision.escalated


def test_build_stack_path_policy_requires_path():
    import pytest

    from hive.mcp_server import build_stack

    with pytest.raises(ValueError, match="policy-path"):
        build_stack(policy="path")
