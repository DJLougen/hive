"""Hive: CPU-side action routing, context compression, and causal memory.

Fewer LLM calls at the same resolve rate — mechanical agent-loop decisions
(list/read/run-tests) run on the CPU; only reasoning escalates to the model.

A meta-package that wires together three components:

* **busyBee-cpu** — CPU-only action routing (replaces LLM calls for obvious
  mechanical decisions).
* **honey-comb**  — Inline context compression (CORE/DISTILL/COMPACT/DROP/STALE/
  ESCALATE) so the LLM only sees the honey, never the wax.
* **rust-brain**  — Timestamped graph memory with Hermes integration.

All three are developed independently; Hive just glues them into a single
ergonomic Python API and a single benchmark surface.

Typical usage::

    from hive import HiveStack

    stack = HiveStack()                 # picks up both siblings
    decision = stack.route(state)       # busyBee-cpu
    compressed = stack.compress(message)# honey-comb
    stack.remember("endpoint", "/v1/x") # rust-brain

See :mod:`hive.stack` for the orchestrator.
"""

from __future__ import annotations

__version__ = "0.7.0"
__all__ = [
    "CascadeRoutingPolicy",
    "HiveConfig",
    "HiveStack",
    "HiveUnavailable",
    "RouteDecision",
    "SemanticBackendError",
    "SemanticRoutingPolicy",
    "__version__",
]


def __getattr__(name: str):  # PEP 562 — lazy import
    if name == "HiveStack" or name in ("HiveUnavailable", "RouteDecision"):
        from hive.stack import HiveStack, HiveUnavailable, RouteDecision

        return {
            "HiveStack": HiveStack,
            "HiveUnavailable": HiveUnavailable,
            "RouteDecision": RouteDecision,
        }[name]
    if name == "HiveConfig":
        from hive.config import HiveConfig

        return HiveConfig
    if name in ("CascadeRoutingPolicy", "SemanticRoutingPolicy", "SemanticBackendError"):
        from hive.cascade_policy import CascadeRoutingPolicy
        from hive.semantic_backend import SemanticBackendError
        from hive.semantic_policy import SemanticRoutingPolicy

        return {
            "CascadeRoutingPolicy": CascadeRoutingPolicy,
            "SemanticRoutingPolicy": SemanticRoutingPolicy,
            "SemanticBackendError": SemanticBackendError,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
