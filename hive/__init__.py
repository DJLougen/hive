"""Hive: unified agent memory & context compression stack.

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

__version__ = "0.5.0"
__all__ = ["HiveStack", "__version__"]


def __getattr__(name: str):  # PEP 562 — lazy import
    if name == "HiveStack":
        from hive.stack import HiveStack

        return HiveStack
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
