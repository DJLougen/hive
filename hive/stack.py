"""Hive orchestrator: glues busyBee-cpu, honey-comb, and rust-brain together.

The orchestrator is intentionally thin — it imports the three components at
runtime so a deployment can swap any one of them (e.g. replace the Python
busyBee policy with a remote RPC, or replace the Python rust-brain with the
upcoming Rust core) without touching call sites.

The default HoneyComb configuration is the *production* mode
(``thread_safe=True``, ``metrics_enabled=True``) so behaviour matches what
honey-comb ships in its own readme. Callers who want the high-performance
fast path (no locks, no metrics) pass ``HoneyComb(...)`` explicitly. If
honey-comb is not installed at all, the in-repo ``hive.rule_fast`` is the
last-ditch fallback so the stack remains usable on a fresh checkout.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from hive.rust_brain import EdgeKind, MemoryNode, RustBrain

__all__ = ["HiveStack", "RouteDecision", "CompressedTurn", "HiveUnavailable"]

_log = logging.getLogger("hive.stack")


class HiveUnavailable(RuntimeError):
    """Raised when a component is missing and no fallback is available."""


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RouteDecision:
    """Output of :meth:`HiveStack.route`."""

    tool: str
    args: dict[str, Any]
    confidence: float
    escalated: bool
    source: str  # "busybee" | "honeycomb-escalate" | "fallback"


@dataclass(slots=True)
class CompressedTurn:
    """Output of :meth:`HiveStack.compress`."""

    role: str
    content: str
    label: str
    original_tokens: int
    compressed_tokens: int

    @property
    def ratio(self) -> float:
        if self.compressed_tokens == 0:
            return 0.0
        return self.original_tokens / self.compressed_tokens


# ---------------------------------------------------------------------------
# Default honey-comb constructor
# ---------------------------------------------------------------------------


def _default_honey_comb() -> Any:
    """Pick a context compressor.

    * If ``honeycomb`` is importable, use its production-mode
      ``HoneyComb(thread_safe=True, metrics_enabled=True)`` — matches the
      readme's ~17k msg/s number.
    * Otherwise, fall back to the in-repo ``hive.rule_fast.RuleFastHoneyComb``
      so the stack remains runnable on a fresh checkout. The fallback is
      ~5x faster on edge devices than the ML classifier path.
    """
    try:
        from honeycomb import HoneyComb  # type: ignore[import-not-found]

        return HoneyComb(thread_safe=True, metrics_enabled=True)
    except Exception as exc:  # pragma: no cover - import guard
        from hive.rule_fast import RuleFastHoneyComb

        _log.warning(
            "honey-comb import failed (%s); using hive.rule_fast fallback.", exc
        )
        return RuleFastHoneyComb()


# ---------------------------------------------------------------------------
# Stack
# ---------------------------------------------------------------------------


class HiveStack:
    """Unified facade over busyBee-cpu + honey-comb + rust-brain.

    Parameters
    ----------
    busybee_policy:
        A trained :class:`busybee_cpu.CpuActionPolicy` (or compatible object
        exposing ``.predict(row) -> dict``). If ``None``, busyBee is disabled
        and every call falls through to the LLM.
    honey_comb:
        A :class:`honeycomb.HoneyComb` (or :class:`hive.rule_fast.RuleFastHoneyComb`)
        instance. If ``None``, a sensible default is chosen by
        :func:`_default_honey_comb`.
    rust_brain:
        A :class:`hive.rust_brain.RustBrain` instance (or compatible). If
        ``None``, an in-memory store is created automatically.
    """

    def __init__(
        self,
        *,
        busybee_policy: Any | None = None,
        honey_comb: Any | None = None,
        rust_brain: RustBrain | None = None,
    ) -> None:
        self.busybee = busybee_policy
        self.comb = honey_comb if honey_comb is not None else _default_honey_comb()
        self.brain = rust_brain or RustBrain()

    # -- busyBee-cpu --------------------------------------------------------

    def route(self, state: Mapping[str, Any]) -> RouteDecision:
        """Decide which tool to invoke next. CPU-only."""
        if self.busybee is None:
            return RouteDecision(
                tool="escalate",
                args={"reason": "no busybee policy loaded"},
                confidence=0.0,
                escalated=True,
                source="fallback",
            )
        t0 = time.perf_counter()
        action = self.busybee.predict(dict(state))
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        _log.debug("busybee routed to %s in %.2fms", action.get("tool"), elapsed_ms)
        return RouteDecision(
            tool=str(action.get("tool") or "escalate"),
            args=dict(action.get("args") or {}),
            confidence=float(action.get("confidence", 0.0)),
            escalated=bool(action.get("escalated", False)),
            source="busybee",
        )

    # -- honey-comb ---------------------------------------------------------

    def compress(self, role: str, content: str, *, content_type: str | None = None) -> CompressedTurn:
        """Run a single message through the inline compression hot loop.

        ``content_type`` is optional; the compressor will infer it from the
        content if you do not provide one. Pass a value from
        ``honeycomb.labels.ContentType`` to skip inference.
        """
        # The Message type lives in either honey-comb or rule_fast; we
        # use whichever the active compressor expects. Both have the
        # same constructor signature.
        from dataclasses import fields

        # Sniff whether the active compressor's Message wants a
        # ``content_type`` kwarg. If not, drop it.
        msg_fields = {f.name for f in fields(self._message_cls())}
        kwargs: dict[str, Any] = {"role": role, "content": content}
        if "content_type" in msg_fields and content_type is not None:
            kwargs["content_type"] = content_type

        out = self.comb.process(self._message_cls()(**kwargs))
        # Honey-Comb returns a ``Label`` enum (``.value`` is the string);
        # rule_fast already returns a plain string. Coerce uniformly.
        label = getattr(out.label, "value", out.label)
        return CompressedTurn(
            role=out.role,
            content=out.content,
            label=label,
            original_tokens=out.original_tokens,
            compressed_tokens=out.compressed_tokens,
        )

    def compress_many(self, turns: Sequence[tuple[str, str]]) -> list[CompressedTurn]:
        """Compress a full transcript in order."""
        return [self.compress(role, content) for role, content in turns]

    def _message_cls(self) -> type:
        """Return the Message class used by the active compressor.

        The active compressor exposes it as ``self.comb.Message`` (honey-comb)
        or it is the in-repo ``hive.rule_fast.Message``. We cache the lookup
        so the hot path stays cheap.
        """
        cached = getattr(self, "_msg_cls", None)
        if cached is not None:
            return cached
        # honey-comb exposes a top-level ``Message`` import; the active
        # compressor's module path tells us which one to use.
        module = type(self.comb).__module__
        if module.startswith("honeycomb"):
            from honeycomb import Message  # type: ignore[import-not-found]

            cached = Message
        else:
            from hive.rule_fast import Message  # type: ignore[import-not-found]

            cached = Message
        self._msg_cls = cached
        return cached

    # -- rust-brain ---------------------------------------------------------

    def remember(
        self,
        key: str,
        value: Any,
        *,
        trust: float = 1.0,
        tags: Sequence[str] | None = None,
        caused_by: Sequence[str] | None = None,
    ) -> MemoryNode:
        """Write a memory node, optionally causal-linked to earlier nodes."""
        edges = {EdgeKind.CAUSED_BY: list(caused_by)} if caused_by else None
        return self.brain.remember(
            key=key,
            value=value,
            trust=trust,
            tags=tags or (),
            edges=edges,
        )

    def recall(self, key: str, default: Any = None) -> Any:
        return self.brain.recall(key, default)

    # -- composition --------------------------------------------------------

    def step(self, state: Mapping[str, Any], transcript: Sequence[tuple[str, str]]) -> dict[str, Any]:
        """End-to-end Hive step: route → compress → write the decision to brain.

        Returns a dict with the routing decision, the compressed last turn
        and the brain's latest write (if any). Designed to be easy to feed
        into a vLLM / llama.cpp request.
        """
        decision = self.route(state)
        last_role, last_content = transcript[-1] if transcript else ("user", "")
        compressed = self.compress(last_role, last_content) if last_content else None
        # Persist the routing decision so downstream agents can audit it.
        self.remember(
            key=f"decision:{state.get('step', 0)}",
            value={
                "tool": decision.tool,
                "args": decision.args,
                "confidence": decision.confidence,
                "escalated": decision.escalated,
            },
            tags=("hive", "routing"),
        )
        return {
            "decision": decision,
            "compressed": compressed,
            "stats": self.stats(),
        }

    # -- telemetry ----------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        return {
            "brain": self.brain.stats(),
            "comb": self.comb.get_stats() if hasattr(self.comb, "get_stats") else {},
        }
