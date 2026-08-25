#!/usr/bin/env python3
"""Model Context Protocol (MCP) server for Hive.

Exposes Hive operations as MCP tools so Claude Desktop, Cursor, and other
MCP-compatible clients can call Hive directly.

Usage::

    python scripts/hive_mcp_server.py --transport sse --port 8080

Tools exposed:
- ``hive_route`` — route a decision locally
- ``hive_compress`` — compress bloated context
- ``hive_remember`` — store a memory
- ``hive_recall`` — retrieve a memory
- ``hive_search`` — search memories by tag
"""

from __future__ import annotations

import argparse
import json

from hive import HiveStack
from hive.rule_fast import RuleFastHoneyComb

try:
    from mcp.server import Server  # type: ignore[import]
    from mcp.types import TextContent, Tool  # type: ignore[import]

    _HAS_MCP = True
except Exception:
    _HAS_MCP = False


def _make_server() -> Server:
    server = Server("hive-mcp")
    stack = HiveStack(honey_comb=RuleFastHoneyComb())

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="hive_route",
                description="Route a mechanical decision locally (skip LLM)",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "goal": {"type": "string"},
                        "available_tools": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["goal", "available_tools"],
                },
            ),
            Tool(
                name="hive_compress",
                description="Compress bloated context before LLM",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "role": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["role", "content"],
                },
            ),
            Tool(
                name="hive_remember",
                description="Store a memory in the graph",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "value": {},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["key", "value"],
                },
            ),
            Tool(
                name="hive_recall",
                description="Retrieve a memory by key",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                    },
                    "required": ["key"],
                },
            ),
            Tool(
                name="hive_search",
                description="Search memories by tag",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "tag": {"type": "string"},
                        "min_trust": {"type": "number"},
                    },
                    "required": ["tag"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        if name == "hive_route":
            state = {
                "goal": arguments["goal"],
                "available_tools": arguments.get("available_tools", []),
            }
            d = stack.route(state)
            return [TextContent(type="text", text=json.dumps({
                "tool": d.tool,
                "args": d.args,
                "confidence": d.confidence,
                "escalated": d.escalated,
                "source": d.source,
            }))]

        if name == "hive_compress":
            c = stack.compress(arguments["role"], arguments["content"])
            return [TextContent(type="text", text=json.dumps({
                "role": c.role,
                "content": c.content,
                "label": c.label,
            }))]

        if name == "hive_remember":
            stack.remember(
                arguments["key"],
                arguments["value"],
                tags=set(arguments.get("tags", [])),
            )
            return [TextContent(type="text", text="OK")]

        if name == "hive_recall":
            val = stack.recall(arguments["key"])
            return [TextContent(type="text", text=json.dumps({"value": val}))]

        if name == "hive_search":
            nodes = stack.brain.search(
                tag=arguments["tag"],
                min_trust=arguments.get("min_trust", 0.0),
            )
            return [TextContent(type="text", text=json.dumps(
                [n.to_dict() for n in nodes]
            ))]

        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return server


def main(argv: list[str] | None = None) -> int:
    if not _HAS_MCP:
        print("ERROR: mcp package not installed. Run: pip install mcp")
        return 1

    p = argparse.ArgumentParser(description="Hive MCP server")
    p.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    p.add_argument("--port", type=int, default=8080)
    args = p.parse_args(argv)

    server = _make_server()

    if args.transport == "stdio":
        from mcp.server.stdio import stdio_server  # type: ignore[import]
        async def run():
            async with stdio_server() as (read, write):
                await server.run(read, write, server.create_initialization_options())
        import asyncio
        asyncio.run(run())
    else:
        from mcp.server.sse import SseServerTransport  # type: ignore[import]
        from starlette.applications import Starlette  # type: ignore[import]
        from starlette.routing import Route  # type: ignore[import]

        transport = SseServerTransport("/messages/")
        async def handle_sse(request):
            async with transport.connect_sse(
                request.scope, request.receive, request._send
            ) as (read, write):
                await server.run(read, write, server.create_initialization_options())

        app = Starlette(routes=[Route("/sse", handle_sse)])
        import uvicorn  # type: ignore[import]
        uvicorn.run(app, host="127.0.0.1", port=args.port)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
