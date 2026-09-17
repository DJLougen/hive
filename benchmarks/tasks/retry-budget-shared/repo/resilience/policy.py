"""Which failures are worth another attempt, and how long to wait."""

from __future__ import annotations

# Errors that mean "the attempt failed, the operation may still work".
_RETRYABLE = (TimeoutError, ConnectionError, OSError)


def should_retry(exc: BaseException) -> bool:
    """True when another attempt on the same operation is worth making."""
    return isinstance(exc, _RETRYABLE)


def delay_for(attempt: int, base: float = 0.05, factor: float = 2.0,
              cap: float = 1.0) -> float:
    """Seconds to sleep before attempt ``attempt + 1`` (``attempt`` is 1-based).

    Exponential in the number of attempts already made, capped so a long-lived
    budget cannot turn into an unbounded sleep.
    """
    if attempt < 1:
        raise ValueError("attempt numbers start at 1")
    return min(cap, base * factor ** (attempt - 1))
