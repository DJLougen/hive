"""Tests for MCP server wiring (import smoke)."""

from __future__ import annotations

import pytest

from hive.mcp_server import HIVE_MCP_TOOLS, main


def test_mcp_module_importable():
    from hive import mcp_server

    assert hasattr(mcp_server, "make_server")
    assert len(HIVE_MCP_TOOLS) == 5


def test_mcp_main_requires_mcp_package(monkeypatch):
    monkeypatch.setattr("hive.mcp_server._HAS_MCP", False)
    assert main([]) == 1
