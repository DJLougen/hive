"""Install Hive MCP configuration for Cursor, Claude Desktop, and Codex."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hive.mcp_config import (
    append_codex_server,
    claude_desktop_config,
    codex_config_toml,
    cursor_mcp_json,
    default_config_paths,
    merge_mcp_servers,
)


def _write_json(path: Path, payload: dict, *, merge: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if merge and path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        payload = merge_mcp_servers(existing, payload)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_codex(path: Path, snippet: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(append_codex_server(existing, snippet), encoding="utf-8")


def install(*, cursor: bool, claude: bool, codex: bool, project: bool) -> list[Path]:
    paths = default_config_paths()
    written: list[Path] = []

    if cursor:
        target = paths["cursor_project"] if project else paths["cursor_global"]
        _write_json(target, cursor_mcp_json())
        written.append(target)

    if claude:
        import platform

        if platform.system() == "Darwin":
            target = paths["claude_macos"]
        else:
            target = paths["claude_linux"]
        _write_json(target, claude_desktop_config())
        written.append(target)

    if codex:
        target = paths["codex_project"] if project else paths["codex_global"]
        _write_codex(target, codex_config_toml())
        written.append(target)

    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install Hive MCP configs for agent clients")
    parser.add_argument("--cursor", action="store_true", help="Install Cursor mcp.json")
    parser.add_argument("--claude", action="store_true", help="Install Claude Desktop config")
    parser.add_argument("--codex", action="store_true", help="Install Codex config.toml")
    parser.add_argument(
        "--project",
        action="store_true",
        help="Write project-scoped configs (.cursor/, .codex/) instead of global",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Install for Cursor, Claude Desktop, and Codex",
    )
    args = parser.parse_args(argv)

    targets = args.all or args.cursor or args.claude or args.codex
    if not targets:
        parser.error("Pick at least one of --cursor, --claude, --codex, or --all")

    written = install(
        cursor=args.all or args.cursor,
        claude=args.all or args.claude,
        codex=args.all or args.codex,
        project=args.project,
    )
    for path in written:
        print(f"Wrote {path}")
    print("Restart your agent client, then verify Hive tools appear in MCP settings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
