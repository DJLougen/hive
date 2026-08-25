"""Hive MCP server — exposes route/compress/remember tools over stdio or SSE."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from hive import HiveStack
from hive.rule_fast import RuleFastHoneyComb

try:
    from mcp.server.mcpserver import MCPServer

    _HAS_MCP = True
except Exception:
    _HAS_MCP = False
    MCPServer = Any  # type: ignore[misc,assignment]


HIVE_MCP_TOOLS = (
    "hive_route",
    "hive_compress",
    "hive_remember",
    "hive_recall",
    "hive_search",
)


def make_server(stack: HiveStack | None = None) -> MCPServer:
    """Build the MCP server with an optional shared HiveStack instance."""
    if not _HAS_MCP:
        raise RuntimeError("mcp package not installed; pip install 'hive-agent-memory[mcp]'")

    server = MCPServer("hive-mcp")
    hive = stack or HiveStack(honey_comb=RuleFastHoneyComb())

    @server.tool(name="hive_route", description="Route a mechanical decision locally (skip LLM)")
    async def hive_route(goal: str, available_tools: list[str] | None = None) -> str:
        state = {"goal": goal, "available_tools": available_tools or []}
        decision = hive.route(state)
        return json.dumps(
            {
                "tool": decision.tool,
                "args": decision.args,
                "confidence": decision.confidence,
                "escalated": decision.escalated,
                "source": decision.source,
            }
        )

    @server.tool(name="hive_compress", description="Compress bloated context before LLM")
    async def hive_compress(role: str, content: str) -> str:
        compressed = hive.compress(role, content)
        return json.dumps(
            {
                "role": compressed.role,
                "content": compressed.content,
                "label": compressed.label,
            }
        )

    @server.tool(name="hive_remember", description="Store a memory in the graph")
    async def hive_remember(key: str, value: Any, tags: list[str] | None = None) -> str:
        hive.remember(key, value, tags=set(tags or []))
        return "OK"

    @server.tool(name="hive_recall", description="Retrieve a memory by key")
    async def hive_recall(key: str) -> str:
        return json.dumps({"value": hive.recall(key)})

    @server.tool(name="hive_search", description="Search memories by tag")
    async def hive_search(tag: str, min_trust: float = 0.0) -> str:
        nodes = hive.brain.search(tag=tag, min_trust=min_trust)
        return json.dumps([n.to_dict() for n in nodes])

    return server


def main(argv: list[str] | None = None) -> int:
    if not _HAS_MCP:
        print("ERROR: mcp package not installed. Run: pip install 'hive-agent-memory[mcp]'", file=sys.stderr)
        return 1

    parser = argparse.ArgumentParser(description="Hive MCP server (stdio or SSE)")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)

    server = make_server()

    import asyncio

    if args.transport == "stdio":
        asyncio.run(server.run_stdio_async())
        return 0

    asyncio.run(
        server.run_sse_async(
            host="127.0.0.1",
            port=args.port,
            sse_path="/sse",
            message_path="/messages/",
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
