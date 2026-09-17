"""The memoising decorator."""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar

from .key import build_key
from .store import MemoStore

F = TypeVar("F", bound=Callable[..., Any])

_DEFAULT_STORE = MemoStore()


def memoize(fn: F | None = None, *, store: MemoStore | None = None) -> Any:
    """Cache ``fn``'s results under keys built from each call's arguments."""
    def decorate(func: F) -> F:
        memo = store if store is not None else MemoStore()

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = build_key(func.__name__, args, kwargs)
            return memo.get_or_compute(key, lambda: func(*args, **kwargs))

        wrapper.store = memo  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    if fn is not None:
        return decorate(fn)
    return decorate
