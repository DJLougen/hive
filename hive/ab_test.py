"""A/B test harness for policy updates.

Routes a configurable fraction of traffic to a variant policy and compares
outcome rates before promoting the winner.

Usage::

    from hive.ab_test import ABTestHarness

    ab = ABTestHarness(control=old_policy, variant=new_policy, split=0.10)
    decision = ab.route(state)
    ab.record_outcome(decision, actual_action, outcome_type)

    if ab.is_winner():
        ab.promote_variant()  # 100% traffic to winner
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any

from hive.stack import RouteDecision


@dataclass
class ABOutcome:
    """One routing outcome in the A/B test."""

    arm: str  # "control" or "variant"
    tool: str
    actual: str | None
    outcome_type: str
    ts: float = field(default_factory=time.monotonic)


class ABTestHarness:
    """A/B test for policy comparison.

    Parameters
    ----------
    control, variant:
        The two policies to compare (any object with ``.predict(state)``).
    split:
        Fraction of traffic to variant (0.0–1.0).
    min_samples:
        Minimum outcomes before declaring a winner.
    improvement_threshold:
        Required relative improvement to promote variant.
    """

    def __init__(
        self,
        *,
        control: Any,
        variant: Any,
        split: float = 0.10,
        min_samples: int = 100,
        improvement_threshold: float = 0.05,
    ) -> None:
        self._control = control
        self._variant = variant
        self._split = max(0.0, min(1.0, split))
        self._min_samples = min_samples
        self._improvement_threshold = improvement_threshold
        self._rng = random.Random(42)
        self._outcomes: list[ABOutcome] = []
        self._active_arm: str | None = None  # locked after promotion

    def route(self, state: dict[str, Any]) -> RouteDecision:
        """Route via control or variant depending on traffic split."""
        if self._active_arm == "control":
            arm = "control"
        elif self._active_arm == "variant":
            arm = "variant"
        else:
            arm = "variant" if self._rng.random() < self._split else "control"

        policy = self._variant if arm == "variant" else self._control
        action = policy.predict(state)
        decision = RouteDecision(
            tool=str(action.get("tool") or action.get("action") or "escalate"),
            args=dict(action.get("args") or {}),
            confidence=float(action.get("confidence", 0.0)),
            escalated=bool(action.get("escalated", False)),
            source=arm,
        )
        return decision

    def record_outcome(
        self,
        decision: RouteDecision,
        actual_action: str | None,
        outcome_type: str,
    ) -> None:
        self._outcomes.append(
            ABOutcome(
                arm=decision.source,
                tool=decision.tool,
                actual=actual_action,
                outcome_type=outcome_type,
            )
        )

    def stats(self) -> dict[str, Any]:
        """Return current A/B stats."""
        control = [o for o in self._outcomes if o.arm == "control"]
        variant = [o for o in self._outcomes if o.arm == "variant"]
        control_correct = sum(1 for o in control if o.outcome_type in ("correct", "escalated_correctly"))
        variant_correct = sum(1 for o in variant if o.outcome_type in ("correct", "escalated_correctly"))

        control_rate = control_correct / max(len(control), 1)
        variant_rate = variant_correct / max(len(variant), 1)
        improvement = (variant_rate - control_rate) / max(control_rate, 1e-9)

        return {
            "control_samples": len(control),
            "variant_samples": len(variant),
            "control_accuracy": round(control_rate, 4),
            "variant_accuracy": round(variant_rate, 4),
            "improvement": round(improvement, 4),
            "winner": self._active_arm or "undecided",
        }

    def is_winner(self) -> bool:
        """Return True if the variant meets the promotion criteria."""
        if self._active_arm:
            return True
        s = self.stats()
        if s["variant_samples"] < self._min_samples:
            return False
        return s["improvement"] >= self._improvement_threshold

    def promote_variant(self) -> None:
        """Promote variant to 100% traffic."""
        self._active_arm = "variant"

    def rollback(self) -> None:
        """Rollback to 100% control."""
        self._active_arm = "control"


__all__ = ["ABOutcome", "ABTestHarness"]
