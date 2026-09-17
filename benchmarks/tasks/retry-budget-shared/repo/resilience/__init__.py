"""Small resilience toolkit: named attempt budgets, retry policy, and a client."""

from .budget import RetryBudget, RetryBudgetExhausted
from .client import Client
from .policy import delay_for, should_retry
from .registry import budget_for, reset_budgets

__all__ = [
    "Client",
    "RetryBudget",
    "RetryBudgetExhausted",
    "budget_for",
    "delay_for",
    "reset_budgets",
    "should_retry",
]
