"""Tests for streaming API."""

from __future__ import annotations


import pytest

from hive import HiveStack
from hive.rule_fast import RuleFastHoneyComb
from hive.streaming import StreamRouter, StreamCompressor, SSETransport


@pytest.mark.asyncio
async def test_stream_router():
    stack = HiveStack(honey_comb=RuleFastHoneyComb())
    router = StreamRouter(stack)
    chunks = []
    async for chunk in router.route_stream({"goal": "test", "available_tools": []}):
        chunks.append(chunk)
    assert any(c["stage"] == "start" for c in chunks)
    assert any(c["stage"] == "done" for c in chunks)


@pytest.mark.asyncio
async def test_stream_compressor():
    stack = HiveStack(honey_comb=RuleFastHoneyComb())
    comp = StreamCompressor(stack)
    chunks = []
    async for chunk in comp.compress_stream("user", "hello world"):
        chunks.append(chunk)
    assert any(c["stage"] == "start" for c in chunks)
    assert any(c["stage"] == "done" for c in chunks)


def test_sse_format():
    event = {"stage": "done", "event": "route", "id": "1"}
    formatted = SSETransport.format(event)
    assert formatted.startswith("event: route")
    assert "data:" in formatted
