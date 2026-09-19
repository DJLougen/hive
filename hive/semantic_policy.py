"""Semantic routing policy — the backend-agnostic acceptance layer.

This layer does not know whether its backend is Jev or d-Jeff. It:

1. compiles the shared semantic state,
2. asks the backend for a decision,
3. applies Hive's acceptance thresholds,
4. resolves deterministic arguments through the shared resolver,
5. returns a Hive-compatible route — or escalates.

Two invariants worth stating explicitly:

* **Deterministic safety wins.** A high ``safe_to_execute`` never bypasses the
  argument resolver, the write-without-recalled-fix refusal, or the loop guard.
  The model chooses the tool; Hive decides whether it can be executed.
* **Failure escalates.** Any backend error becomes LLM escalation. It is never
  silently treated as a routed decision.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from hive.arg_resolvers import resolve_args
from hive.semantic_backend import SemanticBackendError
from hive.semantic_schema import SCHEMA_VERSION, routing_questions
from hive.semantic_state import compile_state

_log = logging.getLogger("hive.semantic")


class SemanticRoutingPolicy:
    """Route via a :class:`~hive.semantic_backend.SemanticDecisionBackend`."""

    def __init__(
        self,
        backend: Any,
        *,
        tool_threshold: float = 0.90,
        safe_threshold: float = 0.95,
        reasoning_threshold: float = 0.10,
        llm_threshold: float = 0.10,
        max_state_chars: int = 16_000,
        record_sink: Any | None = None,
    ) -> None:
        self.backend = backend
        self.tool_threshold = tool_threshold
        self.safe_threshold = safe_threshold
        self.reasoning_threshold = reasoning_threshold
        self.llm_threshold = llm_threshold
        self.max_state_chars = max_state_chars
        #: Callable receiving one comparison record per decision (or None).
        self.record_sink = record_sink
        self.stats: dict[str, int] = {
            "calls": 0,
            "accepted": 0,
            "escalated_low_confidence": 0,
            "escalated_unsafe": 0,
            "escalated_unresolvable": 0,
            "errors": 0,
        }

    # -- routing -----------------------------------------------------------

    def predict(self, state: dict[str, Any]) -> dict[str, Any]:
        """A Hive ``RoutingPolicy``: return a route dict, or escalate."""
        compiled = compile_state(state, max_chars=self.max_state_chars)
        questions = routing_questions()
        self.stats["calls"] += 1

        try:
            resp = self.backend.decide(state=compiled, questions=questions)
        except SemanticBackendError as exc:
            self.stats["errors"] += 1
            _log.warning("semantic backend failed, escalating: %s", exc)
            self._record(state, compiled, None, "error", None, str(exc))
            return _escalate(f"semantic backend error: {exc}")

        return self._decide_from(state, compiled, resp)

    def _decide_from(
        self,
        state: dict[str, Any],
        compiled: dict[str, Any],
        resp: dict[str, Any],
    ) -> dict[str, Any]:
        answers = resp["answers"]
        tool_answer = answers["tool"]
        probabilities = {str(k): float(v) for k, v in tool_answer["probabilities"].items()}
        tool = str(tool_answer.get("choice") or max(probabilities, key=lambda k: probabilities[k]))
        tool_prob = probabilities.get(tool, 0.0)

        safe = float(answers["safe_to_execute"]["noul"])
        reasoning = float(answers["requires_reasoning"]["noul"])
        needs_llm = float(answers["needs_llm"]["noul"])

        accept = (
            tool_prob >= self.tool_threshold
            and safe >= self.safe_threshold
            and reasoning <= self.reasoning_threshold
            and needs_llm <= self.llm_threshold
        )

        if not accept:
            self.stats["escalated_low_confidence"] += 1
            self._record(state, compiled, resp, "rejected_thresholds", tool, None)
            return _escalate(
                f"semantic: below acceptance (tool={tool_prob:.2f} safe={safe:.2f} "
                f"reasoning={reasoning:.2f} needs_llm={needs_llm:.2f})"
            )

        # Deterministic safety: Hive decides executability, not the model.
        args = resolve_args(tool, state)
        if args is None:
            self.stats["escalated_unresolvable"] += 1
            self._record(state, compiled, resp, "unresolvable_args", tool, None)
            return _escalate(f"semantic: cannot resolve args for {tool!r}")

        self.stats["accepted"] += 1
        self._record(state, compiled, resp, "accepted", tool, None)
        return {
            "tool": tool,
            "args": args,
            "confidence": tool_prob,
            "escalated": False,
            "source": f"semantic:{resp.get('backend', 'unknown')}",
        }

    # -- comparison records ------------------------------------------------

    def _record(
        self,
        state: dict[str, Any],
        compiled: dict[str, Any],
        resp: dict[str, Any] | None,
        verdict: str,
        selected_tool: str | None,
        error: str | None,
    ) -> None:
        if self.record_sink is None:
            return
        try:
            self.record_sink(
                {
                    "id": uuid.uuid4().hex,
                    "group_id": state.get("episode_id") or state.get("task_id"),
                    "schema_version": SCHEMA_VERSION,
                    "state": compiled,
                    "backend": (resp or {}).get("backend"),
                    "model_revision": (resp or {}).get("model_revision"),
                    "prediction": (resp or {}).get("answers"),
                    "selected_tool": selected_tool,
                    "accepted_by_hive": verdict == "accepted",
                    "verdict": verdict,
                    "latency_ms": (resp or {}).get("latency_ms"),
                    "error": error,
                }
            )
        except Exception:
            _log.debug("semantic record sink raised; ignoring", exc_info=True)


def _escalate(reason: str) -> dict[str, Any]:
    return {"tool": "escalate", "args": {"reason": reason}, "escalated": True,
            "confidence": 0.0, "source": "semantic"}


__all__ = ["SemanticRoutingPolicy"]
