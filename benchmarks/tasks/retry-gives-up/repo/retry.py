"""Retry decorator."""

import functools


def retry(times: int, on: tuple[type[BaseException], ...] = (Exception,)):
    """Retry the wrapped function up to `times` total attempts.

    Only exceptions that are instances of `on` are retried; anything else
    propagates immediately. After the last attempt fails, re-raise.
    """

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except on:
                raise

        return wrapper

    return decorator
