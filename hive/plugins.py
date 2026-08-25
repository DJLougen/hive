"""Plugin API for custom compressors, routers, and memory backends.

Usage::

    from hive.plugins import CompressorPlugin, RouterPlugin, register_compressor

    class MyCompressor(CompressorPlugin):
        def compress(self, role: str, content: str) -> CompressedTurn:
            return CompressedTurn(role=role, content=content[:100], label="distill")

    register_compressor("my_compressor", MyCompressor())

Then in HiveStack:

    stack = HiveStack(plugins={"compressor": "my_compressor"})
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from hive.stack import CompressedTurn, RouteDecision

_log = logging.getLogger("hive.plugins")


class CompressorPlugin(ABC):
    """Abstract base for custom compressors."""

    @abstractmethod
    def compress(self, role: str, content: str) -> CompressedTurn:
        """Compress a single message."""
        ...


class RouterPlugin(ABC):
    """Abstract base for custom routers."""

    @abstractmethod
    def route(self, state: dict[str, Any]) -> RouteDecision:
        """Route a decision from agent state."""
        ...


class MemoryBackendPlugin(ABC):
    """Abstract base for custom memory backends."""

    @abstractmethod
    def remember(self, key: str, value: Any, **kwargs: Any) -> Any:
        """Store a value."""
        ...

    @abstractmethod
    def recall(self, key: str, default: Any = None) -> Any:
        """Retrieve a value."""
        ...


# Plugin registry -----------------------------------------------------------

_COMPRESSORS: dict[str, CompressorPlugin] = {}
_ROUTERS: dict[str, RouterPlugin] = {}
_BACKENDS: dict[str, MemoryBackendPlugin] = {}


def register_compressor(name: str, plugin: CompressorPlugin) -> None:
    _COMPRESSORS[name] = plugin
    _log.info("Registered compressor plugin: %s", name)


def register_router(name: str, plugin: RouterPlugin) -> None:
    _ROUTERS[name] = plugin
    _log.info("Registered router plugin: %s", name)


def register_backend(name: str, plugin: MemoryBackendPlugin) -> None:
    _BACKENDS[name] = plugin
    _log.info("Registered memory backend plugin: %s", name)


def get_compressor(name: str) -> CompressorPlugin | None:
    return _COMPRESSORS.get(name)


def get_router(name: str) -> RouterPlugin | None:
    return _ROUTERS.get(name)


def get_backend(name: str) -> MemoryBackendPlugin | None:
    return _BACKENDS.get(name)


def list_plugins() -> dict[str, list[str]]:
    return {
        "compressors": list(_COMPRESSORS),
        "routers": list(_ROUTERS),
        "backends": list(_BACKENDS),
    }


__all__ = [
    "CompressorPlugin",
    "MemoryBackendPlugin",
    "RouterPlugin",
    "get_backend",
    "get_compressor",
    "get_router",
    "list_plugins",
    "register_backend",
    "register_compressor",
    "register_router",
]
