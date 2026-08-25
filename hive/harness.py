"""Harness helpers — load routing policies and wire Hive into eval harnesses."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

_log = logging.getLogger(__name__)


class RoutingPolicy(Protocol):
    """Minimal interface expected by :class:`hive.stack.HiveStack`."""

    def predict(self, state: dict[str, Any]) -> dict[str, Any]: ...


class RuleBasedRoutingPolicy:
    """Rule-based policy that routes mechanical SWE-style decisions to the CPU."""

    def __init__(self) -> None:
        self.stats = {"routed": 0, "escalated": 0}

    def predict(self, state: dict[str, Any]) -> dict[str, Any]:
        goal = str(state.get("goal", "")).lower()
        action_hint = str(state.get("action_hint", "")).lower()
        combined = f"{goal} {action_hint}"

        if any(kw in combined for kw in ["read file", "read_file", "list dir", "grep", "search file", "view code"]):
            self.stats["routed"] += 1
            return {"tool": "read_file", "args": {}, "confidence": 0.95, "escalated": False}
        if any(kw in combined for kw in ["run test", "pytest", "execute test", "check test"]):
            self.stats["routed"] += 1
            return {"tool": "run_tests", "args": {}, "confidence": 0.95, "escalated": False}
        if any(kw in combined for kw in ["apply patch", "git apply", "apply diff", "write fix", "edit file"]):
            self.stats["routed"] += 1
            return {"tool": "apply_patch", "args": {}, "confidence": 0.95, "escalated": False}
        if any(kw in combined for kw in ["install", "pip install", "setup"]):
            self.stats["routed"] += 1
            return {"tool": "run_command", "args": {}, "confidence": 0.90, "escalated": False}

        self.stats["escalated"] += 1
        return {"tool": "escalate", "args": {"reason": "complex reasoning"}, "confidence": 0.5, "escalated": True}


def load_routing_policy(*, model_path: str | Path | None = None) -> RoutingPolicy:
    """Return a trained busyBee policy when available, else a rule-based fallback."""
    if model_path is not None:
        try:
            from busybee_cpu import CpuActionPolicy  # type: ignore[import-not-found]

            policy = CpuActionPolicy.load(str(model_path))
            _log.info("Loaded busyBee model from %s", model_path)
            return policy
        except Exception as exc:
            _log.warning("Failed to load busyBee model %s: %s — using rule-based fallback", model_path, exc)

    try:
        from busybee_cpu import CpuActionPolicy  # type: ignore[import-not-found]  # noqa: F401

        _log.info("busyBee-cpu installed but no model path given — using rule-based fallback")
    except Exception:
        _log.info("busyBee-cpu not installed — using rule-based routing fallback")

    return RuleBasedRoutingPolicy()


def policy_label(policy: RoutingPolicy) -> str:
    """Human-readable label for logging and eval reports."""
    cls = type(policy).__name__
    if cls == "RuleBasedRoutingPolicy":
        return "rule-based"
    return cls
