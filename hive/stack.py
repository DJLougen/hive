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
from hive.telemetry import Telemetry
from hive.feedback import FeedbackBuffer, RoutingOutcome, OutcomeType
from hive.policy_updater import PolicyUpdater
from hive.schemas import validate_state
from hive.config import HiveConfig
from hive.ratelimit import RateLimiter
from hive.circuitbreaker import CircuitBreaker

__all__ = [
    "HiveStack",
    "RouteDecision",
    "CompressedTurn",
    "HiveUnavailable",
    "Telemetry",
    "FeedbackBuffer",
    "RoutingOutcome",
    "OutcomeType",
]

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
    telemetry:
        Optional :class:`hive.telemetry.Telemetry` instance for metrics.
    feedback_buffer:
        Optional :class:`hive.feedback.FeedbackBuffer` for online learning.
    """

    def __init__(
        self,
        *,
        busybee_policy: Any | None = None,
        honey_comb: Any | None = None,
        rust_brain: RustBrain | None = None,
        telemetry: Telemetry | None = None,
        feedback_buffer: FeedbackBuffer | None = None,
        tenant_id: str = "default",
        validate: bool = False,
        config: HiveConfig | None = None,
        rate_limiter: RateLimiter | None = None,
        max_content_bytes: int | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self.config = config or HiveConfig()
        self.busybee = busybee_policy
        self.comb = honey_comb if honey_comb is not None else _default_honey_comb()
        # Auto-detect native Rust backend (hive-cpp)
        self._native = False
        if rust_brain is None:
            import importlib.util
            if importlib.util.find_spec("hive_cpp") is not None:
                self._native = True
        self.brain = rust_brain or RustBrain(
            tenant_id=tenant_id,
            tenant_isolation=self.config.tenant_isolation,
            default_ttl_s=self.config.default_ttl_s,
            max_nodes=self.config.max_memory_nodes,
        )
        self._tenant_id = tenant_id
        self._validate = validate or self.config.validate_inputs
        self.rate_limiter = rate_limiter
        if self.rate_limiter is None and self.config.rate_limit > 0:
            self.rate_limiter = RateLimiter(
                default_capacity=self.config.rate_limit,
                refill_rate=max(self.config.rate_limit / 60.0, 1.0),
            )
        self.circuit_breaker = circuit_breaker
        self._max_content_bytes = (
            max_content_bytes
            if max_content_bytes is not None
            else self.config.max_content_bytes
        )
        self.telemetry = telemetry
        self.feedback = feedback_buffer
        self._policy_updater = PolicyUpdater() if feedback_buffer is not None else None

        # Track last routing decision for feedback
        self._last_state: dict[str, Any] | None = None
        self._last_decision: RouteDecision | None = None

    @property
    def feedback_buffer(self) -> FeedbackBuffer | None:
        """Public accessor for feedback buffer (online learning)."""
        return self.feedback

    # -- busyBee-cpu --------------------------------------------------------

    def route(self, state: Mapping[str, Any]) -> RouteDecision:
        """Decide which tool to invoke next. CPU-only."""
        if self.rate_limiter is not None and not self.rate_limiter.check(self._tenant_id, "route"):
            return RouteDecision(
                tool="escalate",
                args={"reason": "rate limited"},
                confidence=0.0,
                escalated=True,
                source="ratelimit",
            )
        if self._validate:
            validate_state(state)
        # Store state for later feedback
        self._last_state = dict(state)

        if self.busybee is None:
            decision = RouteDecision(
                tool="escalate",
                args={"reason": "no busybee policy loaded"},
                confidence=0.0,
                escalated=True,
                source="fallback",
            )
            if self.telemetry is not None:
                self.telemetry.record_routing(
                    source="fallback",
                    action=decision.tool,
                    confidence=decision.confidence,
                    latency_ms=0.0,
                    escalated=True,
                )
            self._last_decision = decision
            return decision
        t0 = time.perf_counter()
        action = self.busybee.predict(dict(state))
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        _log.debug("busybee routed to %s in %.2fms", action.get("tool"), elapsed_ms)
        decision = RouteDecision(
            tool=str(action.get("tool") or action.get("action") or "escalate"),
            args=dict(action.get("args") or {}),
            confidence=float(action.get("confidence", 0.0)),
            escalated=bool(action.get("escalated", False)),
            source="busybee",
        )
        if self.telemetry is not None:
            self.telemetry.record_routing(
                source="busybee",
                action=decision.tool,
                confidence=decision.confidence,
                latency_ms=elapsed_ms,
                escalated=decision.escalated,
            )
        self._last_decision = decision
        return decision

    # -- honey-comb ---------------------------------------------------------

    def compress(
        self, role: str, content: str, *, content_type: str | None = None
    ) -> CompressedTurn:
        """Run a single message through the inline compression hot loop.

        ``content_type`` is optional; the compressor will infer it from the
        content if you do not provide one. Pass a value from
        ``honeycomb.labels.ContentType`` to skip inference.
        """
        if len(content.encode("utf-8")) > self._max_content_bytes:
            raise ValueError(
                f"compress() content exceeds max_content_bytes ({self._max_content_bytes})"
            )

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

        t0 = time.perf_counter()
        out = self.comb.process(self._message_cls()(**kwargs))
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        # Honey-Comb returns a ``Label`` enum (``.value`` is the string);
        # rule_fast already returns a plain string. Coerce uniformly.
        label = getattr(out.label, "value", out.label)
        result = CompressedTurn(
            role=out.role,
            content=out.content,
            label=label,
            original_tokens=out.original_tokens,
            compressed_tokens=out.compressed_tokens,
        )
        if self.telemetry is not None:
            self.telemetry.record_compression(
                role=out.role,
                label=label,
                original_tokens=out.original_tokens,
                compressed_tokens=out.compressed_tokens,
                latency_ms=elapsed_ms,
            )
        return result

    def compress_many(self, turns: Sequence[tuple[str, str]]) -> list[CompressedTurn]:
        """Compress a full transcript in order."""
        total = sum(len(content.encode("utf-8")) for _, content in turns)
        if total > self._max_content_bytes:
            raise ValueError(
                f"compress_many() total content ({total} bytes) exceeds max_content_bytes ({self._max_content_bytes})"
            )
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
        t0 = time.perf_counter()
        node = self.brain.remember(
            key=key,
            value=value,
            trust=trust,
            tags=tags or (),
            edges=edges,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if self.telemetry is not None:
            self.telemetry.record_memory_write(
                key=key,
                trust=trust,
                has_causal_edge=bool(caused_by),
                has_tags=bool(tags),
                latency_ms=elapsed_ms,
            )
        return node

    def recall(self, key: str, default: Any = None) -> Any:
        t0 = time.perf_counter()
        hit = key in self.brain
        result = self.brain.recall(key, default)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if self.telemetry is not None:
            self.telemetry.record_memory_read(
                key=key,
                hit=hit,
                latency_ms=elapsed_ms,
            )
        return result

    # -- online learning ----------------------------------------------------

    def record_outcome(
        self,
        decision: "RouteDecision | None",
        actual_action: str | None,
        outcome_type: OutcomeType | str,
    ) -> None:
        """Record feedback for a routing decision.

        Parameters
        ----------
        decision:
            The RouteDecision from route(), or None if no decision was made.
        actual_action:
            The action that was actually taken (tool name), or None if
            escalated / unknown.
        outcome_type:
            OutcomeType enum or string name.
        """
        if self.feedback is None:
            _log.warning("record_outcome called but no feedback_buffer configured")
            return

        if decision is None:
            _log.warning("record_outcome called but no routing decision recorded")
            return

        # Normalize outcome_type
        if isinstance(outcome_type, str):
            try:
                outcome_type = OutcomeType(outcome_type)
            except ValueError:
                outcome_type = OutcomeType.UNKNOWN

        if self._last_decision is None or not (
            decision.tool == self._last_decision.tool
            and decision.args == self._last_decision.args
            and decision.source == self._last_decision.source
            and decision.confidence == self._last_decision.confidence
            and decision.escalated == self._last_decision.escalated
        ):
            _log.warning(
                "record_outcome decision does not match the most recent route(); "
                "rejected — possible policy poisoning attempt"
            )
            return  # Reject forged feedback to prevent policy poisoning

        state = dict(self._last_state) if self._last_state else {}

        outcome = RoutingOutcome(
            state=state,
            routed_action=decision.tool if decision else None,
            actual_action=actual_action,
            outcome_type=outcome_type,
        )

        self.feedback.add(outcome)
        _log.debug(
            "Recorded %s outcome for %s (buffer: %d/%d)",
            outcome_type.value,
            decision.tool,
            len(self.feedback),
            self.feedback.capacity,
        )

    def should_update_policy(self) -> bool:
        """Check if feedback buffer has enough outcomes to update policy."""
        if self.feedback is None:
            return False
        return self.feedback.is_full()

    def update_policy(self) -> bool:
        """Update busybee policy from collected feedback.

        Returns True if update succeeded, False otherwise.
        """
        if not self.should_update_policy():
            _log.warning("Cannot update policy: not enough feedback")
            return False

        if self.busybee is None:
            _log.warning("Cannot update policy: no busybee policy loaded")
            return False

        if self._policy_updater is None:
            _log.warning("Cannot update policy: no policy updater configured")
            return False
        if self.feedback is None:
            _log.warning("Cannot update policy: no feedback buffer")
            return False
        batch = self.feedback.get_batch()
        success = self._policy_updater.update(self.busybee, batch)

        if success:
            _log.info(
                "Successfully updated busybee policy from %d outcomes", len(batch)
            )
        else:
            _log.warning("Failed to update busybee policy")

        return success

    # -- composition --------------------------------------------------------

    def step(
        self, state: Mapping[str, Any], transcript: Sequence[tuple[str, str]]
    ) -> dict[str, Any]:
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
        result = {
            "brain": self.brain.stats(),
            "comb": self.comb.get_stats() if hasattr(self.comb, "get_stats") else {},
        }
        if self.telemetry is not None:
            result["telemetry"] = self.telemetry.summary()
        if self.feedback is not None:
            result["feedback"] = self.feedback.summary()
        return result
