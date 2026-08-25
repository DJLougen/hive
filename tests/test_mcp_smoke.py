"""Tests for MCP server wiring (import smoke)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def test_mcp_server_module_importable():
    script = Path(__file__).resolve().parent.parent / "scripts" / "hive_mcp_server.py"
    spec = importlib.util.spec_from_file_location("hive_mcp_server", script)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["hive_mcp_server"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    assert hasattr(module, "_make_server") or hasattr(module, "_HAS_MCP")
