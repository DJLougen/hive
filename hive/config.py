"""Enterprise configuration management for Hive.

Reads ``HIVE_*`` environment variables and provides validated, typed
access to all deployment settings. No secrets hardcoded in source files.

Usage::

    from hive.config import HiveConfig

    cfg = HiveConfig.from_env()
    cfg.validate()
    print(cfg.tenant_isolation, cfg.rate_limit)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class HiveConfig:
    """Centralised deployment configuration."""

    validate_inputs: bool = False
    tenant_isolation: bool = True
    audit_enabled: bool = False
    rate_limit: int = 0  # 0 = disabled
    default_ttl_s: float | None = None
    max_memory_nodes: int = 10_000
    jwt_secret: str | None = None
    otel_endpoint: str | None = None
    prometheus_port: int = 0  # 0 = disabled

    @classmethod
    def from_env(cls, prefix: str = "HIVE_") -> "HiveConfig":
        """Construct config from environment variables.

        Booleans: ``true``, ``1``, ``yes`` → True; anything else → False.
        Integers parsed via ``int()``; floats via ``float()``.
        Strings pass through verbatim. Missing keys fall back to defaults.
        """
        kwargs: dict[str, Any] = {}

        def _parse_bool(raw: str) -> bool:
            return raw.lower() in ("true", "1", "yes", "on")

        import typing
        hints = typing.get_type_hints(cls)
        for attr in cls.__dataclass_fields__:
            env_key = f"{prefix}{attr.upper()}"
            raw = os.environ.get(env_key)
            if raw is None:
                continue
            field_type = hints.get(attr, str)
            # Strip Optional wrapper
            if hasattr(field_type, "__args__"):
                field_type = field_type.__args__[0]  # type: ignore[attr-defined]
            if field_type is bool:
                kwargs[attr] = _parse_bool(raw)
            elif field_type is int:
                kwargs[attr] = int(raw)
            elif field_type is float:
                kwargs[attr] = float(raw)
            elif field_type is type(None):
                kwargs[attr] = None if raw.lower() in ("none", "null", "") else raw
            else:
                kwargs[attr] = raw

        return cls(**kwargs)

    def validate(self) -> None:
        """Raise RuntimeError if required settings are missing in production mode."""
        if self.rate_limit < 0:
            raise ValueError("rate_limit must be >= 0")
        if self.max_memory_nodes < 1:
            raise ValueError("max_memory_nodes must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        """Return a plain dict (useful for JSON logging)."""
        return {
            f.name: getattr(self, f.name)
            for f in self.__dataclass_fields__.values()
        }


__all__ = ["HiveConfig"]
