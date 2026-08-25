"""WebSocket/SSE streaming for real-time Hive operations.

Provides async generators that yield partial results as they are produced,
reducing perceived latency for long agent turns.

Usage::

    from hive.streaming import StreamRouter

    async for chunk in StreamRouter(stack).route_stream(state):
        print(chunk)  # partial decision metadata
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from hive import HiveStack


class StreamRouter:
    """Stream routing decisions in real-time."""

    def __init__(self, stack: HiveStack) -> None:
        self._stack = stack

    async def route_stream(
        self, state: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield routing progress events."""
        yield {"stage": "start", "state": state.get("goal", "")}
        await asyncio.sleep(0)  # yield control

        yield {"stage": "routing", "source": "busybee"}
        decision = self._stack.route(state)
        yield {"stage": "decision", "tool": decision.tool, "confidence": decision.confidence}
        yield {"stage": "done", "decision": {
            "tool": decision.tool,
            "args": decision.args,
            "confidence": decision.confidence,
            "escalated": decision.escalated,
            "source": decision.source,
        }}


class StreamCompressor:
    """Stream compression progress."""

    def __init__(self, stack: HiveStack) -> None:
        self._stack = stack

    async def compress_stream(
        self, role: str, content: str
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield compression progress events."""
        yield {"stage": "start", "role": role, "bytes": len(content.encode("utf-8"))}
        await asyncio.sleep(0)

        yield {"stage": "processing"}
        result = self._stack.compress(role, content)
        yield {"stage": "done", "label": result.label, "ratio": len(content) / max(len(result.content), 1)}


class SSETransport:
    """Format events as Server-Sent Events."""

    @staticmethod
    def format(event: dict[str, Any]) -> str:
        lines = ["data: " + json.dumps(event, ensure_ascii=False)]
        if "id" in event:
            lines.insert(0, f"id: {event['id']}")
        if "event" in event:
            lines.insert(0, f"event: {event['event']}")
        return "\n".join(lines) + "\n\n"


__all__ = ["SSETransport", "StreamCompressor", "StreamRouter"]
