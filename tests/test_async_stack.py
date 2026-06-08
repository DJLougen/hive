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
async def test_async_remember_passes_trust_and_tags():
    stack = AsyncHiveStack()
    await stack.remember("k", "v", trust=0.25, tags={"audit"})
    node = stack.stack.brain.get("k")
    assert node is not None
    assert node.trust == 0.25
    assert node.tags == {"audit"}


@pytest.mark.asyncio
async def test_async_compress_many():
    stack = AsyncHiveStack(honey_comb=RuleFastHoneyComb())
    results = await stack.compress_many([("user", "a"), ("user", "b")])
    assert len(results) == 2
