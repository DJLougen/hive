"""The versioned Hive routing question schema.

This is a **benchmark contract**: Jev and any future d-Jeff backend must be
asked the same questions over the same state, or their results are not
comparable. Bump :data:`SCHEMA_VERSION` when the questions change, and never
silently edit a released version in place.
"""

from __future__ import annotations

from typing import Any

#: Bumped when the question set or their semantics change.
SCHEMA_VERSION = "hive-routing-v1"


def routing_questions() -> dict[str, dict[str, Any]]:
    """The canonical routing question set (a fresh copy each call)."""
    return {
        "tool": {
            "type": "choice",
            "instructions": "Which tool should the agent invoke next?",
            "criteria": {
                "list_files": "Inspect repository contents.",
                "read_file": "Read a known relevant file.",
                "grep": "Search source text or symbols.",
                "run_tests": "Run verification or reproduce a failure.",
                "run_command": "Execute a command.",
                "web": "Use external web research.",
                "write_file": "Modify a file when content is already available.",
                "finish": "Conclude the task.",
            },
        },
        "safe_to_execute": {
            "type": "noul",
            "instructions": (
                "Can this predicted action be executed autonomously without "
                "language-model reasoning?"
            ),
        },
        "requires_reasoning": {
            "type": "noul",
            "instructions": (
                "Does the current state require semantic reasoning by a larger "
                "language model?"
            ),
        },
        "needs_llm": {
            "type": "noul",
            "instructions": "Should Hive escalate this decision to the language model?",
        },
    }


__all__ = ["SCHEMA_VERSION", "routing_questions"]
