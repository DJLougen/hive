"""Generate MCP client configuration snippets for Hive."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

SERVER_NAME = "hive"


def resolve_hive_mcp_command() -> list[str]:
    """Return argv prefix to launch the MCP server."""
    exe = shutil.which("hive-mcp")
    if exe:
        return [exe]
    return [sys.executable, "-m", "hive.mcp_server"]


def cursor_mcp_json(*, command: list[str] | None = None) -> dict[str, Any]:
    cmd = command or resolve_hive_mcp_command()
    return {
        "mcpServers": {
            SERVER_NAME: {
                "type": "stdio",
                "command": cmd[0],
                "args": cmd[1:],
            }
        }
    }


def claude_desktop_config(*, command: list[str] | None = None) -> dict[str, Any]:
    cmd = command or resolve_hive_mcp_command()
    return {
        "mcpServers": {
            SERVER_NAME: {
                "command": cmd[0],
                "args": cmd[1:],
            }
        }
    }


def codex_config_toml(*, command: list[str] | None = None) -> str:
    cmd = command or resolve_hive_mcp_command()
    lines = [f"[mcp_servers.{SERVER_NAME}]", f'command = "{cmd[0]}"']
    if len(cmd) > 1:
        args = ", ".join(json.dumps(part) for part in cmd[1:])
        lines.append(f"args = [{args}]")
    return "\n".join(lines) + "\n"


def default_config_paths() -> dict[str, Path]:
    home = Path.home()
    return {
        "cursor_global": home / ".cursor" / "mcp.json",
        "cursor_project": Path.cwd() / ".cursor" / "mcp.json",
        "claude_macos": home
        / "Library"
        / "Application Support"
        / "Claude"
        / "claude_desktop_config.json",
        "claude_linux": home / ".config" / "Claude" / "claude_desktop_config.json",
        "codex_global": home / ".codex" / "config.toml",
        "codex_project": Path.cwd() / ".codex" / "config.toml",
    }


def merge_mcp_servers(existing: dict[str, Any], hive_entry: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    servers = dict(merged.get("mcpServers") or {})
    servers.update(hive_entry.get("mcpServers") or {})
    merged["mcpServers"] = servers
    return merged


def append_codex_server(existing_toml: str, hive_toml: str) -> str:
    marker = f"[mcp_servers.{SERVER_NAME}]"
    if marker in existing_toml:
        return existing_toml
    if existing_toml and not existing_toml.endswith("\n"):
        existing_toml += "\n"
    return existing_toml + "\n" + hive_toml
