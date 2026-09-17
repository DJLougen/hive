"""The four places a setting value can come from."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping


class Layer(ABC):
    """One source of values. ``name`` identifies its precedence class."""

    name: str = "layer"

    @abstractmethod
    def items(self) -> Mapping[str, Any]:
        """The raw values this layer sets."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{type(self).__name__}(name={self.name!r}, keys={sorted(self.items())})"


class Defaults(Layer):
    """Values from the schema, used when no other layer sets the key."""

    name = "default"

    def __init__(self, defaults: Mapping[str, Any]) -> None:
        self._defaults = dict(defaults)

    def items(self) -> Mapping[str, Any]:
        return self._defaults


class FileLayer(Layer):
    """A ``key = value`` config file."""

    name = "file"

    def __init__(self, text: str = "") -> None:
        self._values: dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition("=")
            if sep:
                self._values[key.strip()] = value.strip()

    def items(self) -> Mapping[str, Any]:
        return self._values


class EnvLayer(Layer):
    """Environment variables, optionally behind a prefix.

    Environment values are always strings; the schema decides what they mean.
    """

    name = "env"

    def __init__(self, environ: Mapping[str, str] | None = None, prefix: str = "APP_") -> None:
        self._prefix = prefix
        self._values = {
            key[len(prefix):].lower(): value
            for key, value in (environ or {}).items()
            if key.startswith(prefix)
        }

    def items(self) -> Mapping[str, Any]:
        return self._values


class ExplicitLayer(Layer):
    """Values handed in by the caller for this call alone."""

    name = "explicit"

    def __init__(self, values: Mapping[str, Any] | None = None) -> None:
        self._values = dict(values or {})

    def items(self) -> Mapping[str, Any]:
        return self._values
