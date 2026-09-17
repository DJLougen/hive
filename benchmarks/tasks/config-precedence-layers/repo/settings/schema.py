"""What a setting is, and how a raw string becomes one."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"not a boolean: {value!r}")


_COERCERS: dict[str, Callable[[Any], Any]] = {
    "str": str,
    "int": int,
    "bool": _as_bool,
}


def coerce(value: Any, kind: str) -> Any:
    """Convert a raw value (usually a string from a file or the environment)."""
    try:
        coercer = _COERCERS[kind]
    except KeyError:
        raise ValueError(f"unknown setting type {kind!r}") from None
    if isinstance(value, str) and kind != "str":
        value = value.strip()
    return coercer(value)


@dataclass(frozen=True)
class Setting:
    """One named setting: its type and the value used when nothing sets it."""

    name: str
    kind: str = "str"
    default: Any = None


@dataclass
class Schema:
    """The set of settings an application knows about."""

    settings: list[Setting] = field(default_factory=list)

    def defines(self, name: str) -> bool:
        return any(s.name == name for s in self.settings)

    def type_of(self, name: str) -> str:
        for s in self.settings:
            if s.name == name:
                return s.kind
        raise KeyError(name)

    def default_of(self, name: str) -> Any:
        for s in self.settings:
            if s.name == name:
                return s.default
        raise KeyError(name)
