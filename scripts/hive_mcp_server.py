#!/usr/bin/env python3
"""Backward-compatible launcher for the Hive MCP server."""

from hive.mcp_server import main

if __name__ == "__main__":
    raise SystemExit(main())
