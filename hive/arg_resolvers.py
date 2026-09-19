"""Deterministic argument resolution — the shared execution path.

Every routing tier (rule, CPU, semantic) produces a *tool*; this module turns a
tool plus observable state into concrete arguments, or ``None`` when the
arguments cannot be derived from state alone.

The semantic layer deliberately does **not** get its own resolvers: a semantic
backend (Jev today, d-Jeff later) chooses *which tool*, and this module stays
authoritative about *whether the call can be executed*. That keeps a model
probability from ever overriding deterministic policy (see the project's
integration plan, "Preserve deterministic Hive safety").

The original implementation lived in :mod:`hive.cpu_policy`; it is defined here
and re-exported there so existing imports keep working.
"""

from __future__ import annotations

from typing import Any


def resolve_args(tool: str, state: dict[str, Any]) -> dict[str, Any] | None:
    """Fill tool args from observable state, or None if not derivable."""
    if tool in ("list_files", "run_tests", "finish"):
        return {}
    if tool == "read_file":
        path = state.get("suggested_read")
        if not path or path in (state.get("files_read") or []):
            return None
        return {"path": path}
    if tool == "grep":
        # Grep the symbol under test: the failing test name or the module
        # the test file imports are both legitimately observable.
        sig = str(state.get("fail_signature") or "")
        for tok in sig.replace("::", " ").split():
            if tok.startswith("test_"):
                return {"pattern": tok}
        return None
    if tool == "write_file":
        fix = state.get("recalled_fix")
        if fix and fix.get("path") and fix.get("content") is not None:
            return {"path": fix["path"], "content": fix["content"]}
        return None
    return None


__all__ = ["resolve_args"]
