"""MCP client integration tests — configs and server wiring."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from hive.mcp_config import (
    claude_desktop_config,
    codex_config_toml,
    cursor_mcp_json,
    merge_mcp_servers,
)
from hive.mcp_server import HIVE_MCP_TOOLS, make_server


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_cursor_mcp_json_shape():
    payload = cursor_mcp_json(command=["hive-mcp"])
    assert "mcpServers" in payload
    hive = payload["mcpServers"]["hive"]
    assert hive["command"] == "hive-mcp"
    assert hive["type"] == "stdio"


def test_claude_desktop_config_shape():
    payload = claude_desktop_config(command=["/usr/bin/python", "-m", "hive.mcp_server"])
    hive = payload["mcpServers"]["hive"]
    assert hive["command"] == "/usr/bin/python"
    assert hive["args"] == ["-m", "hive.mcp_server"]


def test_codex_config_toml_shape():
    text = codex_config_toml(command=["hive-mcp"])
    assert "[mcp_servers.hive]" in text
    assert 'command = "hive-mcp"' in text


def test_merge_mcp_servers_preserves_existing():
    merged = merge_mcp_servers(
        {"mcpServers": {"other": {"command": "echo"}}},
        cursor_mcp_json(command=["hive-mcp"]),
    )
    assert "other" in merged["mcpServers"]
    assert "hive" in merged["mcpServers"]


def test_repo_cursor_config_is_valid_json():
    path = REPO_ROOT / ".cursor" / "mcp.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["mcpServers"]["hive"]["command"] == "hive-mcp"


def test_integration_snippets_exist():
    assert (REPO_ROOT / "integrations/mcp/claude_desktop_config.snippet.json").exists()
    assert (REPO_ROOT / "integrations/mcp/codex.config.snippet.toml").exists()


def test_claude_snippet_matches_generator():
    snippet = json.loads(
        (REPO_ROOT / "integrations/mcp/claude_desktop_config.snippet.json").read_text(encoding="utf-8")
    )
    assert snippet == claude_desktop_config(command=["hive-mcp"])


def test_codex_snippet_matches_generator():
    raw = (REPO_ROOT / "integrations/mcp/codex.config.snippet.toml").read_text(encoding="utf-8")
    # Strip comment lines for comparison with the generator output
    snippet_body = "\n".join(line for line in raw.splitlines() if not line.strip().startswith("#")).strip() + "\n"
    assert snippet_body == codex_config_toml(command=["hive-mcp"])


@pytest.mark.asyncio
async def test_mcp_server_lists_hive_tools():
    pytest.importorskip("mcp")
    server = make_server()
    tools = await server.list_tools()
    names = {tool.name for tool in tools}
    assert names == set(HIVE_MCP_TOOLS)


@pytest.mark.asyncio
async def test_mcp_server_hive_remember_recall():
    pytest.importorskip("mcp")
    server = make_server()
    result = await server.call_tool("hive_remember", {"key": "k", "value": "v"})
    assert result.content[0].text == "OK"
    recalled = await server.call_tool("hive_recall", {"key": "k"})
    assert json.loads(recalled.content[0].text)["value"] == "v"


@pytest.mark.asyncio
async def test_mcp_server_over_stdio():
    """Spawn the server as a subprocess and exercise list_tools / call_tool."""
    pytest.importorskip("mcp")
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(command=sys.executable, args=["-m", "hive.mcp"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            assert names == set(HIVE_MCP_TOOLS)

            remembered = await session.call_tool(
                "hive_remember",
                {"key": "stdio-test", "value": "via-stdio"},
            )
            assert remembered.content[0].text == "OK"

            recalled = await session.call_tool("hive_recall", {"key": "stdio-test"})
            assert json.loads(recalled.content[0].text)["value"] == "via-stdio"
