"""The entry point applications actually call."""

from __future__ import annotations

from typing import Any, Mapping

from .layers import ordered_layers
from .resolve import resolve
from .schema import Schema
from .sources import Defaults, EnvLayer, ExplicitLayer, FileLayer


class Settings:
    """Resolved settings, plus where each value came from."""

    def __init__(self, resolved: Mapping[str, tuple[Any, str]]) -> None:
        self._resolved = dict(resolved)

    def get(self, name: str, fallback: Any = None) -> Any:
        entry = self._resolved.get(name)
        return entry[0] if entry is not None else fallback

    def source_of(self, name: str) -> str | None:
        entry = self._resolved.get(name)
        return entry[1] if entry is not None else None

    def source_of(self, name: str) -> str | None:
        """The layer that decided one setting, or None when nothing set it."""
        entry = self._resolved.get(name)
        return entry[1] if entry is not None else None

    def sources(self) -> dict[str, str]:
        """The winning layer for every known setting that has a value."""
        return {name: source for name, (_value, source) in self._resolved.items()}

    def as_dict(self) -> dict[str, Any]:
        return {name: value for name, (value, _src) in self._resolved.items()}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Settings({self.as_dict()!r})"


def load_settings(schema: Schema, *, file: str | None = None,
                  environ: Mapping[str, str] | None = None,
                  explicit: Mapping[str, Any] | None = None,
                  prefix: str = "APP_") -> Settings:
    """Build the four layers and resolve them.

    Every layer is always present, even when empty, so the precedence order is
    a property of the *layers*, not of which of them the caller happened to
    supply values for.
    """
    layers = [
        Defaults({s.name: s.default for s in schema.settings if s.default is not None}),
        FileLayer(file or ""),
        EnvLayer(environ, prefix=prefix),
        ExplicitLayer(explicit),
    ]
    return Settings(resolve(ordered_layers(layers), schema))
