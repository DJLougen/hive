"""Layer precedence.

Lowest first: a value from a later layer replaces one from an earlier layer.
Explicit values are the caller's own instruction for this call, so they sit at
the top; a built-in default is only a fallback, so it sits at the bottom.
"""

from __future__ import annotations

from typing import Any, Iterable

LAYER_ORDER: tuple[str, ...] = ("default", "file", "env", "explicit")


def layer_rank(name: str) -> int:
    """Position of a layer in the precedence order; higher wins."""
    try:
        return LAYER_ORDER.index(name)
    except ValueError:
        raise ValueError(f"unknown layer {name!r}") from None


def ordered_layers(layers: Iterable[Any]) -> list[Any]:
    """Sort layers from lowest precedence to highest."""
    return sorted(layers, key=lambda layer: layer_rank(layer.name))
