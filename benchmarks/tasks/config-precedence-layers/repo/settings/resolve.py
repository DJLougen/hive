"""Merging layers into resolved values."""

from __future__ import annotations

from typing import Any, Iterable

from .schema import Schema, coerce

# Layer precedence, lowest first. Later entries replace earlier ones.
_PRECEDENCE: dict[str, int] = {"default": 0, "file": 1, "explicit": 2, "env": 3}


def resolve(layers: Iterable[Any], schema: Schema) -> dict[str, tuple[Any, str]]:
    """Resolve every known key to ``(value, name of the layer that won)``."""
    resolved: dict[str, tuple[Any, str]] = {}
    for layer in sorted(layers, key=lambda layer: _PRECEDENCE[layer.name]):
        for key, raw in layer.items().items():
            if not schema.defines(key):
                continue  # a value for a setting this app does not know about
            resolved[key] = (coerce(raw, schema.type_of(key)), layer.name)
    return resolved
