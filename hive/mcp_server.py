"""Hive MCP server — exposes route/compress/remember tools over stdio or SSE."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from hive import HiveStack
from hive.harness import load_routing_policy, policy_label
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



def build_stack(policy: str = "rule", policy_path: str | None = None) -> HiveStack:
    """Build the HiveStack the MCP server routes against.

    ``policy="rule"`` (default) installs the rule-based routing policy so
    ``hive_route`` actually routes mechanical decisions instead of always
    escalating. ``policy="path"`` loads a trained
    :class:`hive.cpu_policy.CPURouterPolicy` from ``policy_path``.
    """
    if policy == "path":
        if not policy_path:
            raise ValueError("--policy path requires --policy-path PATH")
        from hive.cpu_policy import CPURouterPolicy

        router: Any = CPURouterPolicy.load(policy_path)
    elif policy == "rule":
        router = load_routing_policy()
    else:
        raise ValueError(f"unknown policy {policy!r} (expected 'rule' or 'path')")
    return HiveStack(busybee_policy=router, honey_comb=RuleFastHoneyComb())

def make_server(stack: HiveStack | None = None) -> MCPServer:
    """Build the MCP server with an optional shared HiveStack instance."""
    if not _HAS_MCP:
        raise RuntimeError("mcp package not installed; pip install 'hive-agent-memory[mcp]'")

    server = MCPServer("hive-mcp")
    hive = stack or build_stack()

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
                "policy": policy_label(hive.busybee) if hive.busybee is not None else "none",
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
    parser = argparse.ArgumentParser(description="Hive MCP server (stdio or SSE)")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--policy",
        choices=["rule", "path"],
        default="rule",
        help="routing policy: 'rule' (default) or 'path' (trained model)",
    )
    parser.add_argument(
        "--policy-path",
        default=None,
        help="path to a trained CPURouterPolicy .joblib (with --policy path)",
    )
    args = parser.parse_args(argv)

    if not _HAS_MCP:
        print("ERROR: mcp package not installed. Run: pip install 'hive-agent-memory[mcp]'", file=sys.stderr)
        return 1

    server = make_server(stack=build_stack(policy=args.policy, policy_path=args.policy_path))

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
