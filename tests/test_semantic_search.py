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
    index.add(node)
    # add() stores vectors keyed "key:node_id"; without sentence-transformers
    # no model exists, so the index must hold no vector for the node.
    assert not any(k.rsplit(":", 1)[0] == "auth" for k in index._vectors)
    # The node is still reachable through the tag-scan fallback.
    assert [n.key for n, _ in index.search("bug")] == ["auth"]
