"""Tests for the plugin API."""

from __future__ import annotations


from hive.plugins import (
    CompressorPlugin,
    RouterPlugin,
    register_compressor,
    register_router,
    get_compressor,
    get_router,
    list_plugins,
)
from hive.stack import CompressedTurn, RouteDecision


class DummyCompressor(CompressorPlugin):
    def compress(self, role: str, content: str) -> CompressedTurn:
        return CompressedTurn(role=role, content=content[:10], label="distill")


class DummyRouter(RouterPlugin):
    def route(self, state: dict) -> RouteDecision:
        return RouteDecision(
            tool="test",
            args={},
            confidence=1.0,
            escalated=False,
            source="test",
        )


def test_register_and_get_compressor():
    c = DummyCompressor()
    register_compressor("dummy", c)
    assert get_compressor("dummy") is c


def test_register_and_get_router():
    r = DummyRouter()
    register_router("dummy", r)
    assert get_router("dummy") is r


def test_list_plugins():
    plugins = list_plugins()
    assert "dummy" in plugins["compressors"]
    assert "dummy" in plugins["routers"]
