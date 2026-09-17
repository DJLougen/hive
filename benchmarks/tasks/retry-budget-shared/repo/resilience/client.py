"""A client that retries through a named attempt budget."""

from __future__ import annotations

import time
from typing import Any, Callable

from .budget import RetryBudget, RetryBudgetExhausted
from .policy import delay_for, should_retry


class Client:
    """Calls a function, retrying retryable failures within a budget.

    ``budget_name`` selects which budget the attempts are charged to. Callers
    that pass the same name are spending from the same pot, which is the point
    of naming it: a process-wide cap on how hard a downstream service is
    hammered, however many call sites there are.
    """

    def __init__(self, budget_name: str = "default", limit: int = 3,
                 sleeper: Callable[[float], Any] | None = None) -> None:
        self.budget_name = budget_name
        self.limit = limit
        self.sleeper = sleeper if sleeper is not None else time.sleep
        self.budget = RetryBudget(limit, name=budget_name)

    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run ``fn``, retrying until the budget refuses another attempt."""
        last: BaseException | None = None
        attempts = 0
        while True:
            if not self.budget.spend():
                break
            try:
                return fn(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001 - re-raised below
                if not should_retry(exc):
                    raise
                last = exc
                attempts += 1
                self.sleeper(delay_for(attempts))
                self.sleeper(delay_for(attempts))  # settle before trying again
        raise RetryBudgetExhausted(
            f"retry budget {self.budget_name!r} is spent "
            f"({self.budget.spent}/{self.budget.limit} attempts)"
        )