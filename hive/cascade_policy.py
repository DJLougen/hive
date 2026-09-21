"""Cascade routing — deterministic tiers first, semantic second, LLM last.

``CascadeRoutingPolicy`` composes a fast policy (the CPU router or the rule-based
state machine) with a semantic policy, and knows nothing about which semantic
model is underneath. It must never contain an ``if jev`` or ``if djeff``.

Modes:

``off``
    Only the fast policy runs (plus whatever the fast policy escalates).
``shadow``
    The semantic policy *is asked* and its decisions are recorded, but it cannot
    change routing. Use this to build comparison data and tune thresholds.
``cascade``
    Fast policy first; on escalation, the semantic policy is tried; if that also
    escalates, the decision goes to the LLM.
``compare``
    The primary semantic policy may influence routing *and* a second (shadow)
    semantic policy is asked the same state under the same schema, so their
    decisions can be compared pairwise. Only the primary can change the route.
"""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger("hive.cascade")

VALID_MODES = ("off", "shadow", "cascade", "compare")


class CascadeRoutingPolicy:
    """Compose a fast deterministic policy with a semantic one."""

    def __init__(
        self,
        *,
        fast_policy: Any,
        semantic_policy: Any | None = None,
        shadow_semantic_policy: Any | None = None,
        mode: str = "cascade",
        require_semantic: bool = False,
    ) -> None:
        if mode not in VALID_MODES:
            raise ValueError(f"mode must be one of {VALID_MODES}, got {mode!r}")
        self.fast_policy = fast_policy
        self.semantic_policy = semantic_policy
        #: Second backend, asked the same state in ``compare`` mode. It never
        #: influences routing — that is the whole point of a paired comparison.
        self.shadow_semantic_policy = shadow_semantic_policy
        self.mode = mode
        self.require_semantic = require_semantic
        self.stats: dict[str, int] = {
            "fast_accepted": 0,
            "semantic_accepted": 0,
            "semantic_shadow_only": 0,
            "escalated": 0,
            "generation_bypass": 0,
            "shadow_compared": 0,
        }

    def predict(self, state: dict[str, Any]) -> dict[str, Any]:
        fast = self.fast_policy.predict(dict(state))

        # Generation bypass: patch synthesis is not a routing decision, so a
        # semantic call would be wasted (and cannot resolve args anyway).
        if _is_generation(fast):
            self.stats["generation_bypass"] += 1
            return fast

        if _routed(fast):
            self.stats["fast_accepted"] += 1
            if self.mode == "shadow" and self.semantic_policy is not None:
                # Observe only: the semantic decision is recorded, never
                # applied — and an observational call that raises must not
                # break the fast route either.
                try:
                    self.semantic_policy.predict(dict(state))
                except Exception:
                    _log.warning("shadow semantic policy raised; ignoring",
                                 exc_info=True)
                self.stats["semantic_shadow_only"] += 1
            self._compare_shadow(state)
            return fast

        if self.mode == "off" or self.semantic_policy is None:
            self.stats["escalated"] += 1
            if self.require_semantic and self.semantic_policy is None:
                return {"tool": "escalate", "args": {"reason": "semantic backend required but absent"},
                        "escalated": True, "confidence": 0.0, "source": "cascade"}
            return fast

        self._compare_shadow(state)
        if self.mode == "shadow":
            # Observation must preserve even the fast policy's escalation:
            # an accepted shadow prediction is not permission to execute it,
            # and an observational call that raises must not break routing.
            try:
                self.semantic_policy.predict(dict(state))
            except Exception:
                _log.warning("shadow semantic policy raised; ignoring",
                             exc_info=True)
            self.stats["semantic_shadow_only"] += 1
            self.stats["escalated"] += 1
            return fast
        semantic = self.semantic_policy.predict(dict(state))
        if _routed(semantic):
            self.stats["semantic_accepted"] += 1
            return semantic
        self.stats["escalated"] += 1
        return semantic


    def _compare_shadow(self, state: dict[str, Any]) -> None:
        """Ask the shadow backend the same state; never apply its answer."""
        if self.mode != "compare" or self.shadow_semantic_policy is None:
            return
        try:
            self.shadow_semantic_policy.predict(dict(state))
        except Exception:
            # A shadow that fails must not interfere with the primary route.
            _log.warning("compare shadow policy raised; ignoring", exc_info=True)
        else:
            self.stats["shadow_compared"] += 1


def _routed(decision: dict[str, Any]) -> bool:
    """True when a policy produced an executable route (not an escalation)."""
    return bool(decision) and not decision.get("escalated") and decision.get("tool") not in (None, "escalate")


def _is_generation(decision: dict[str, Any]) -> bool:
    return "generation" in str(decision.get("args", {}).get("reason", "")).lower()


__all__ = ["VALID_MODES", "CascadeRoutingPolicy"]
