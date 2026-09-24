"""Tests for async HiveStack."""

from __future__ import annotations

import pytest

from hive.async_stack import AsyncHiveStack
from hive.rule_fast import RuleFastHoneyComb


@pytest.mark.asyncio
async def test_async_route():
    stack = AsyncHiveStack(honey_comb=RuleFastHoneyComb())
    d = await stack.route({"goal": "test", "available_tools": []})
    assert d.tool == "escalate"


@pytest.mark.asyncio
async def test_async_compress():
    stack = AsyncHiveStack(honey_comb=RuleFastHoneyComb())
    c = await stack.compress("user", "hello world")
    assert c.role == "user"


@pytest.mark.asyncio
async def test_async_remember_recall():
    stack = AsyncHiveStack()
    await stack.remember("k", "v")
    assert await stack.recall("k") == "v"


@pytest.mark.asyncio
async def test_async_compress_many():
    stack = AsyncHiveStack(honey_comb=RuleFastHoneyComb())
    results = await stack.compress_many([("user", "a"), ("user", "b")])
    assert len(results) == 2


@pytest.mark.asyncio
async def test_async_compress_many_rejects_oversized_batch():
    """Each message may be under max_content_bytes while the batch exceeds it."""
    stack = AsyncHiveStack(honey_comb=RuleFastHoneyComb())
    stack._stack._max_content_bytes = 50
    turns = [("user", "x" * 40) for _ in range(10)]
    with pytest.raises(ValueError, match="exceeds max_content_bytes"):
        await stack.compress_many(turns)
