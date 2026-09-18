"""Event-sourced state with snapshots."""

from .store import Snapshot, fold, load_state

__all__ = ["Snapshot", "fold", "load_state"]
