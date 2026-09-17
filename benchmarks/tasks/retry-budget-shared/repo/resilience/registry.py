"""Named budgets: the cap belongs to the name, not to the caller."""

from __future__ import annotations

from .budget import RetryBudget

_BUDGETS: dict[str, RetryBudget] = {}


def budget_for(name: str, limit: int = 3) -> RetryBudget:
    """Return the budget registered under ``name``.

    The first caller decides the limit; later callers — including callers that
    ask for a different limit — get that same budget object back, so every
    attempt charged against the name counts against one shared cap.
    """
    return RetryBudget(limit, name=name)


def reset_budgets() -> None:
    """Drop every registered budget (used between runs and in tests)."""
    _BUDGETS.clear()
