"""The attempt budget itself."""

from __future__ import annotations


class RetryBudgetExhausted(RuntimeError):
    """A caller wanted another attempt that the budget could not pay for."""


class RetryBudget:
    """Counts the attempts charged against one cap.

    The counter lives here, on the object every caller of a name shares, so
    that the cap is a property of the *work*, not of whoever happens to be
    calling.
    """

    def __init__(self, limit: int, name: str = "") -> None:
        if limit < 1:
            raise ValueError("a retry budget must allow at least one attempt")
        self.name = name
        self.limit = limit
        self.spent = 0

    @property
    def remaining(self) -> int:
        """Attempts left. Never negative, even if something over-charges."""
        return max(0, self.limit - self.spent)

    def spend(self) -> bool:
        """Charge one attempt.

        Returns ``False`` without charging when the cap is already reached, so
        a caller can refuse a retry instead of silently exceeding it.
        """
        if self.spent >= self.limit:
            return False
        self.spent += 1
        return True

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"RetryBudget(name={self.name!r}, limit={self.limit}, spent={self.spent})"
