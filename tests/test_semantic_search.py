"""Tests for semantic search."""

from __future__ import annotations

from hive.rust_brain import RustBrain
from hive.semantic_search import SemanticIndex


def test_semantic_index_no_model():
    brain = RustBrain()
    brain.remember("key", "value", tags={"tag1"})
    index = SemanticIndex(brain, model="nonexistent-model-xxx")
    # Model fails to load, search falls back to a tag scan. The documented
    # return type is list[(MemoryNode, score)], so assert the node is really
    # found — not just that the list is non-negative in length.
    results = index.search("tag1")
    assert [(node.key, score) for node, score in results] == [("key", 1.0)]


def test_semantic_index_add():
    brain = RustBrain()
    node = brain.remember("auth", "login bug", tags={"bug"})
    index = SemanticIndex(brain)
    # Without sentence-transformers, add() is a no-op but doesn't crash
    index.add(node)
    assert node.key == "auth"
