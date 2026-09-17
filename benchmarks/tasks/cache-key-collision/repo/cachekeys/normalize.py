"""Turning argument values into the text a cache key is made of."""

from __future__ import annotations

from typing import Any


def canonical(value: Any) -> str:
    """Render one argument value so equal values render equally.

    Containers are rendered structurally rather than through ``repr`` so that
    a value nested several levels down is still comparable.
    """
    if isinstance(value, dict):
        return "{" + ",".join(
            f"{canonical(k)}:{canonical(v)}" for k, v in value.items()
        ) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical(v) for v in value) + "]"
    if isinstance(value, (set, frozenset)):
        return "(" + ",".join(canonical(v) for v in value) + ")"
    return str(value)
