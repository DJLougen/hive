"""Cache keys.

A key names a *call*: the function plus the argument values it was made with.
Two calls that ask for the same thing must land on the same key, whatever the
caller's spelling, and two calls that ask for different things must not.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .normalize import canonical


def build_key(fn_name: str, args: Sequence[Any] = (), kwargs: Mapping[str, Any] | None = None) -> str:
    positional = "(" + ",".join(canonical(a) for a in args) + ")"
    keywords = "{" + ",".join(
        f"{name}={canonical(value)}" for name, value in (kwargs or {}).items()
    ) + "}"
    return f"{fn_name}|{positional}|{keywords}"
