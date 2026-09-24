"""Capacity eviction in RustBrain must be observable, not silent."""

from __future__ import annotations

import logging

from hive.rust_brain import RustBrain


def test_eviction_is_counted_and_logged(caplog) -> None:
    brain = RustBrain(max_nodes=2)

    with caplog.at_level(logging.WARNING, logger="hive.rust_brain"):
        brain.remember("k1", {"v": 1})
        brain.remember("k2", {"v": 2})
        assert brain.stats()["evictions"] == 0
        brain.remember("k3", {"v": 3})

    assert brain.stats()["evictions"] == 1
    assert len(brain) == 2
    assert "k1" not in brain  # oldest entry is the one dropped
    assert "k3" in brain
    assert any("at capacity" in r.getMessage() for r in caplog.records)


def test_eviction_counts_every_entry_dropped() -> None:
    brain = RustBrain(max_nodes=1)
    for i in range(5):
        brain.remember(f"k{i}", {"v": i})

    assert len(brain) == 1
    assert brain.stats()["evictions"] == 4
    assert "k4" in brain


def test_eviction_drops_empty_string_key_at_capacity() -> None:
    brain = RustBrain(max_nodes=2)
    brain.remember("", "empty")
    brain.remember("a", "1")
    brain.remember("b", "2")
    assert "" not in brain
    assert brain.recall("") is None
    assert "a" in brain
    assert "b" in brain
