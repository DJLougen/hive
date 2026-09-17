"""Backend selection for HiveStack (Python reference vs native hive-cpp)."""

from __future__ import annotations

import json
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
    """Compress one message via hive-cpp.

    The crate's ``rust_compress(text)`` takes the message text only; ``role`` is
    accepted here so the call site in :mod:`hive.stack` stays backend-agnostic.
    Returns the crate payload: ``compressed``, ``original_tokens``,
    ``compressed_tokens``, ``ratio``, ``latency_ms``.
    """
    from hive_cpp import rust_compress  # type: ignore[import-not-found]

    return json.loads(rust_compress(content))


def native_route(state: dict[str, Any], model_json: str) -> dict[str, Any]:
    """Route via hive-cpp's decision tree.

    ``rust_router_decide(model_json, state_json)`` needs a serialized
    ``RouterModel`` and an ``AgentState``; all six ``AgentState`` fields are
    required by serde, so missing keys are filled with empty values.
    The crate returns a ``Decision`` (``action``/``confidence``/``reasoning``/
    ``latency_ms``) — this adapter maps it onto the dict shape
    :meth:`hive.stack.HiveStack.route` expects.
    """
    from hive_cpp import rust_router_decide  # type: ignore[import-not-found]

    agent_state = {
        "goal": str(state.get("goal", "")),
        "step": int(state.get("step", 0)),
        "last_tool": state.get("last_tool"),
        "recent_observations": [str(x) for x in state.get("recent_observations", [])],
        "open_files": [str(x) for x in state.get("open_files", [])],
        "available_tools": [str(x) for x in state.get("available_tools", [])],
    }
    decision = json.loads(rust_router_decide(model_json, json.dumps(agent_state)))
    action = str(decision.get("action", "escalate"))
    return {
        "tool": action,
        "action": action,
        "args": {},
        "confidence": float(decision.get("confidence", 0.0)),
        "escalated": action == "escalate",
        "reasoning": str(decision.get("reasoning", "")),
    }


__all__ = [
    "BackendName",
    "native_compress",
    "native_route",
    "resolve_backend",
]
