"""Build the semantic stack from configuration.

This is the only module that maps a backend *name* to a backend *class*. Every
other part of Hive sees only the :class:`~hive.semantic_backend.SemanticDecisionBackend`
protocol, which is what makes the Jev → d-Jeff swap a configuration change.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from hive.cascade_policy import CascadeRoutingPolicy
from hive.djeff_backend import DJeffBackend
from hive.jev_backend import DEFAULT_ENDPOINT, JevBackend
from hive.semantic_backend import SemanticBackendError
from hive.semantic_policy import SemanticRoutingPolicy

_log = logging.getLogger("hive.semantic")

#: Environment variable holding the Jev credential (never logged).
JEV_API_KEY_ENV = "HIVE_JEV_API_KEY"


def make_backend(
    name: str,
    *,
    config: Any | None = None,
    client: Any | None = None,
) -> Any:
    """Instantiate a named semantic backend.

    Raises :class:`SemanticBackendError` for an unknown name, or for ``jev`` when
    no credential/endpoint is configured — the caller should treat that as a
    fail-safe (escalate), not as an implicit fallback.
    """
    key = (name or "").strip().lower()
    if key == "jev":
        model = getattr(config, "jev_model", None) or "jev-latest"
        endpoint = getattr(config, "jev_endpoint", None) or DEFAULT_ENDPOINT
        api_key = os.environ.get(JEV_API_KEY_ENV)
        if client is None and not api_key:
            raise SemanticBackendError(
                f"jev backend selected but {JEV_API_KEY_ENV} is unset"
            )
        return JevBackend(client=client, model=model, endpoint=endpoint, api_key=api_key)
    if key == "djeff":
        return DJeffBackend(model=getattr(config, "djeff_model", None), client=client)
    raise SemanticBackendError(f"unknown semantic backend {name!r} (expected 'jev' or 'djeff')")


def build_semantic_stack(
    config: Any,
    *,
    fast_policy: Any,
    clients: dict[str, Any] | None = None,
    record_sink: Any | None = None,
) -> CascadeRoutingPolicy | Any:
    """Return the policy Hive should route with, per ``config.semantic_*``.

    ``off``/disabled returns ``fast_policy`` unchanged, so callers can install the
    result unconditionally.
    """
    clients = clients or {}
    mode = (getattr(config, "semantic_mode", "off") or "off").lower()
    enabled = bool(getattr(config, "semantic_enabled", False))

    if not enabled or mode == "off":
        return fast_policy

    primary_name = getattr(config, "semantic_primary", "jev") or "jev"
    backend = make_backend(primary_name, config=config, client=clients.get(primary_name))
    semantic = SemanticRoutingPolicy(
        backend,
        tool_threshold=float(getattr(config, "semantic_tool_threshold", 0.90)),
        safe_threshold=float(getattr(config, "semantic_safe_threshold", 0.95)),
        reasoning_threshold=float(getattr(config, "semantic_reasoning_threshold", 0.10)),
        llm_threshold=float(getattr(config, "semantic_llm_threshold", 0.10)),
        max_state_chars=int(getattr(config, "semantic_max_state_chars", 16_000)),
        record_sink=record_sink,
    )

    cascade_mode = "cascade" if mode in ("cascade", "compare") else mode
    return CascadeRoutingPolicy(
        fast_policy=fast_policy,
        semantic_policy=semantic,
        mode=cascade_mode,
    )


__all__ = ["JEV_API_KEY_ENV", "build_semantic_stack", "make_backend"]
