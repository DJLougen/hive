"""Compile the agent-loop state into the shared semantic representation.

Both backends must see the *same* semantic state, otherwise a Jev-vs-d-Jeff
comparison is measuring the compiler, not the models. This module is that single
compiler.

The output is deliberately small and structural: workflow counters, the recent
observations the harness already distilled, the files the agent has seen, and the
tool vocabulary. It contains no labels and nothing the model has to guess.
"""

from __future__ import annotations

from typing import Any

#: Canonical tool vocabulary the semantic layer may choose from.
SEMANTIC_TOOLS: tuple[str, ...] = (
    "list_files",
    "read_file",
    "grep",
    "run_tests",
    "run_command",
    "web",
    "write_file",
    "finish",
)


def compile_state(
    state: dict[str, Any],
    *,
    max_observations: int = 8,
    max_files: int = 32,
    max_chars: int = 16_000,
) -> dict[str, Any]:
    """Build the semantic view of ``state``.

    Bounded on purpose: a semantic request is charged by size, and an unbounded
    transcript would make two backends' costs incomparable.
    """
    observations = list(state.get("recent_observations") or [])
    if state.get("fail_signature"):
        observations.append(str(state["fail_signature"]))

    files = [str(p) for p in (state.get("files_read") or [])]

    compiled: dict[str, Any] = {
        "goal": str(state.get("goal") or ""),
        "workflow": {
            "step": int(state.get("step") or state.get("turn") or 0),
            "last_tool": str(state.get("last_tool") or "none"),
            "previous_tool": str(state.get("prev2_tool") or "none"),
            "tests_run": int(state.get("tests_run") or 0),
            "tests_passed": state.get("tests_passed"),
            "writes": int(state.get("writes") or 0),
            "listed": bool(state.get("listed")),
            "memory_hit": bool(state.get("memory_hit")),
            "verify_pending": bool(state.get("verify_pending")),
        },
        "recent_observations": observations[-max_observations:],
        "known_files": files[:max_files],
        "available_tools": list(SEMANTIC_TOOLS),
    }

    # Hard character budget: trim the free-text fields first, never the counters.
    while _size(compiled) > max_chars and compiled["recent_observations"]:
        compiled["recent_observations"].pop(0)
    while _size(compiled) > max_chars and compiled["known_files"]:
        compiled["known_files"].pop()
    if _size(compiled) > max_chars:
        compiled["goal"] = compiled["goal"][: max(0, max_chars // 4)]
    return compiled


def _size(obj: Any) -> int:
    import json

    return len(json.dumps(obj, default=str))


__all__ = ["SEMANTIC_TOOLS", "compile_state"]
