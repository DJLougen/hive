"""Feedback collection for online learning in busybee.

This module provides:
- OutcomeType: Classification of routing outcomes
- RoutingOutcome: Captures what happened after a routing decision
- FeedbackBuffer: Collects outcomes until policy update threshold
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class OutcomeType(Enum):
    """Classification of routing outcomes."""

    CORRECT = "correct"
    WRONG_ACTION = "wrong_action"
    ESCALATED_CORRECTLY = "escalated_correctly"
    ESCALATED_INCORRECTLY = "escalated_incorrectly"
    UNKNOWN = "unknown"


@dataclass
class RoutingOutcome:
    """A single routing outcome with ground truth feedback.

    Attributes:
        state: The agent state when routing was performed
        routed_action: What busybee routed to (None if escalated)
        actual_action: What actually happened (ground truth)
        outcome_type: Classification of the outcome
    """

    state: dict[str, Any]
    routed_action: str | None
    actual_action: str | None
    outcome_type: OutcomeType

    def is_correct(self) -> bool:
        return self.outcome_type in (OutcomeType.CORRECT, OutcomeType.ESCALATED_CORRECTLY)

    def is_wrong_action(self) -> bool:
        return self.outcome_type == OutcomeType.WRONG_ACTION

    def is_incorrect_escalation(self) -> bool:
        return self.outcome_type == OutcomeType.ESCALATED_INCORRECTLY


class FeedbackBuffer:
    """Buffer to collect routing outcomes before updating policy."""

    def __init__(self, capacity: int = 1000):
        self.capacity = capacity
        self.buffer: list[RoutingOutcome] = []

    def _append(self, outcome: RoutingOutcome) -> None:
        if len(self.buffer) >= self.capacity:
            self.buffer.pop(0)
        self.buffer.append(outcome)

    def record(self, outcome: RoutingOutcome) -> bool:
        self._append(outcome)
        return self.is_full()

    def add(self, outcome: RoutingOutcome) -> None:
        self._append(outcome)

    def is_full(self) -> bool:
        return len(self.buffer) >= self.capacity

    def get_batch(self) -> list[RoutingOutcome]:
        batch = self.buffer[:]
        self.buffer.clear()
        return batch

    def size(self) -> int:
        return len(self.buffer)

    def clear(self) -> None:
        self.buffer.clear()

    def stats(self) -> dict[str, Any]:
        if not self.buffer:
            return {"size": 0, "capacity": self.capacity, "outcome_distribution": {}}
        outcome_counts: dict[str, int] = {}
        for outcome in self.buffer:
            key = outcome.outcome_type.value
            outcome_counts[key] = outcome_counts.get(key, 0) + 1
        total = len(self.buffer)
        return {
            "size": total,
            "capacity": self.capacity,
            "outcome_distribution": outcome_counts,
            "outcome_rates": {k: v / total for k, v in outcome_counts.items()},
        }

    def summary(self) -> dict[str, Any]:
        outcome_counts: dict[str, int] = {}
        for outcome in self.buffer:
            key = outcome.outcome_type.value
            outcome_counts[key] = outcome_counts.get(key, 0) + 1
        total = len(self.buffer)
        rates = {k: v / total for k, v in outcome_counts.items()} if total > 0 else {}
        return {
            "size": total,
            "capacity": self.capacity,
            "is_full": self.is_full(),
            "summary": {
                "total_outcomes": total,
                "by_outcome": outcome_counts,
                "by_outcome_rate": rates,
            },
        }

    def get_outcomes(self) -> list[RoutingOutcome]:
        return list(self.buffer)

    def __len__(self) -> int:
        return len(self.buffer)

    def __repr__(self) -> str:
        return f"FeedbackBuffer(size={len(self)}, capacity={self.capacity})"
