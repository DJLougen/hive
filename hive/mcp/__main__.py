"""``python -m hive.mcp`` — run the Hive MCP server or install client configs."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    if not args or args[0] in ("server", "run"):
        from hive.mcp_server import main as server_main

        server_argv = args[1:] if args and args[0] in ("server", "run") else args
        return server_main(server_argv)

    if args[0] == "install":
        from hive.mcp_install import main as install_main

        return install_main(args[1:])

    if args[0] in ("-h", "--help"):
        print(
            "usage: python -m hive.mcp [server|install] [options]\n\n"
            "  (default)  Start the Hive MCP stdio server (same as hive-mcp)\n"
            "  install    Write Cursor / Claude / Codex MCP configs\n"
            "  server     Explicit alias for the stdio server\n\n"
            "Examples:\n"
            "  python -m hive.mcp\n"
            "  python -m hive.mcp install --all\n"
            "  hive-mcp-install --cursor --project\n"
        )
        return 0

    print(f"Unknown command: {args[0]!r}. Try: python -m hive.mcp --help", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
