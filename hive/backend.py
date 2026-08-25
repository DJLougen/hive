"""Backend selection for HiveStack (Python reference vs native hive-cpp)."""

from __future__ import annotations

import os
from typing import Any, Literal

BackendName = Literal["python", "native", "auto"]


def resolve_backend(explicit: BackendName | None = None) -> BackendName:
    """Resolve the active backend from env or explicit override."""
    if explicit is not None and explicit != "auto":
        return explicit
    env = os.environ.get("HIVE_BACKEND", "auto").strip().lower()
    if env in ("python", "native"):
        return env  # type: ignore[return-value]
    if _native_available():
        return "native"
    return "python"


def _native_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("hive_cpp") is not None


def native_compress(role: str, content: str) -> dict[str, Any]:
    """Compress via hive-cpp when installed."""
    from hive_cpp import rust_compress  # type: ignore[import-not-found]

    return rust_compress(role, content)


def native_memory_store(key: str, value: Any) -> dict[str, Any]:
    from hive_cpp import rust_memory_store  # type: ignore[import-not-found]

    return rust_memory_store(key, value)


def native_memory_retrieve(key: str) -> Any:
    from hive_cpp import rust_memory_retrieve  # type: ignore[import-not-found]

    return rust_memory_retrieve(key)


def native_route(state: dict[str, Any]) -> dict[str, Any]:
    from hive_cpp import rust_router_decide  # type: ignore[import-not-found]

    return rust_router_decide(state)


__all__ = [
    "BackendName",
    "native_compress",
    "native_memory_retrieve",
    "native_memory_store",
    "native_route",
    "resolve_backend",
]
