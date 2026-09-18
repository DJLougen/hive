"""Rebuilding state from an event log, with snapshots to skip the prefix."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Snapshot:
    """A materialized state plus how many log events it already covers."""

    state: int
    covered: int  # number of leading log events folded into `state`


def fold(state: int, event: int) -> int:
    """Apply one event to the state."""
    return state + event


def load_state(log: list[int], snapshot: Snapshot | None = None) -> int:
    """Rebuild the counter from ``log``, seeding from ``snapshot`` when given.

    The snapshot is trusted: the whole log is folded on top of it.
    """
    state = snapshot.state if snapshot is not None else 0
    for event in log:
        state = fold(state, event)
    return state
