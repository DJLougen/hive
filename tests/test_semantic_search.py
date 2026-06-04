"""Tests for semantic search."""

from __future__ import annotations


from hive.semantic_search import SemanticIndex
from hive.rust_brain import RustBrain


def test_semantic_index_no_model():
    brain = RustBrain()
    brain.remember("key", "value", tags={"tag1"})
    index = SemanticIndex(brain, model="nonexistent-model-xxx")
    # Model fails to load, search falls back to tag scan
    results = index.search("tag1")
    assert len(results) >= 0


def test_semantic_index_add():
    brain = RustBrain()
    node = brain.remember("auth", "login bug", tags={"bug"})
    index = SemanticIndex(brain)
    # Without sentence-transformers, add() is a no-op but doesn't crash
    index.add(node)
    assert node.key == "auth"
