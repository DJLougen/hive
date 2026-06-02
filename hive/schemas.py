"""Enterprise-grade Pydantic schema validation for Hive public API.

Provides strong typing and runtime validation for all inputs to
:class:`HiveStack` so corrupted or malicious state dictionaries fail
loudly at the boundary rather than silently corrupting downstream logic.
"""

from __future__ import annotations

from typing import Any

try:
    from pydantic import BaseModel, Field, ValidationError  # type: ignore[import-not-found]

    _HAS_PYDANTIC = True
except Exception:  # pragma: no cover
    BaseModel = object  # type: ignore[misc,assignment]
    ValidationError = Exception  # type: ignore[misc,assignment]
    _HAS_PYDANTIC = False


class AgentState(BaseModel if _HAS_PYDANTIC else object):  # type: ignore[valid-type,misc]
    """Validated agent state passed to :meth:`HiveStack.route`."""

    goal: str = Field(default="", min_length=0, max_length=4096)
    step: int = Field(default=0, ge=0)
    last_tool: str | None = Field(default=None)
    recent_observations: list[str] = Field(default_factory=list)
    open_files: list[str] = Field(default_factory=list)
    available_tools: list[str] = Field(default_factory=list)

    model_config = {"extra": "allow"}  # Allow additional keys for extensibility


class RouteDecisionOut(BaseModel if _HAS_PYDANTIC else object):  # type: ignore[valid-type,misc]
    """Validated routing decision returned by :meth:`HiveStack.route`."""

    tool: str = Field(default="escalate", min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    escalated: bool = Field(default=False)
    source: str = Field(default="fallback", min_length=1)


class MemoryNodeIn(BaseModel if _HAS_PYDANTIC else object):  # type: ignore[valid-type,misc]
    """Validated memory write input."""

    key: str = Field(min_length=1, max_length=256)
    value: Any
    trust: float = Field(default=1.0, ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)


class _NoPydanticGuard:
    """No-op validator when pydantic is not installed."""

    @staticmethod
    def validate_state(state: Any) -> dict[str, Any]:
        return dict(state)

    @staticmethod
    def validate_memory(key: str, value: Any, *, trust: float = 1.0) -> dict[str, Any]:
        return {"key": key, "value": value, "trust": trust}


_validator = _NoPydanticGuard()
if _HAS_PYDANTIC:
    _validator = None  # type: ignore[assignment]  # replaced below


def validate_state(state: Any) -> dict[str, Any]:
    """Validate and normalize an agent state dict.

    Returns the validated dict on success. Raises ``ValidationError`` on
    failure. If pydantic is not installed the check is a no-op.
    """
    if not _HAS_PYDANTIC:
        return dict(state)
    try:
        parsed = AgentState(**state)
        return parsed.model_dump()
    except Exception:
        raise


def validate_memory(key: str, value: Any, *, trust: float = 1.0) -> dict[str, Any]:
    """Validate and normalize a memory write."""
    if not _HAS_PYDANTIC:
        return {"key": key, "value": value, "trust": trust}
    try:
        parsed = MemoryNodeIn(key=key, value=value, trust=trust)
        return parsed.model_dump()
    except Exception:
        raise


__all__ = [
    "AgentState",
    "RouteDecisionOut",
    "MemoryNodeIn",
    "validate_state",
    "validate_memory",
    "ValidationError",
]
