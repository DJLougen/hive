"""Tests for the rust-brain reference implementation.

Covers the invariants the Rust port (`hive-cpp`) must preserve:
monotonic timestamps, edge semantics, the snapshot format, and the
causal-write convenience.
"""

from __future__ import annotations

import json

import pytest

from hive.rust_brain import EdgeKind, MemoryNode, RustBrain, TimestampRegression


def test_remember_then_recall():
    brain = RustBrain()
    brain.remember("endpoint", "/v1/chat", trust=0.9)
    assert brain.recall("endpoint") == "/v1/chat"
    assert brain.get("endpoint").trust == pytest.approx(0.9)


def test_recall_returns_default_for_missing_key():
    brain = RustBrain()
    assert brain.recall("missing", default="fallback") == "fallback"


def test_recall_returns_stored_none():
    brain = RustBrain()
    brain.remember("nullable", None)
    assert brain.recall("nullable") is None
    assert brain.recall("nullable", default="fallback") is None


def test_monotonic_timestamp_regression_raises():
    brain = RustBrain()
    # Write with HLC (1, 1000, "node1")
    brain.remember("a", 1, hlc=(1, 1000, "node1"))
    # Attempt to write with earlier HLC (0, 999, "node1") should raise
    with pytest.raises(TimestampRegression):
        brain.remember("a", 2, hlc=(0, 999, "node1"))
    # Stored value unchanged.
    assert brain.recall("a") == 1


def test_supersede_records_edge():
    brain = RustBrain()
    old = brain.remember("k", "v1", ts_ns=1000)
    new = brain.supersede("k", "v2", ts_ns=2000)
    # The new write is what lives in the store under "k".
    assert brain.get("k") is new
    assert new.ts_ns == 2000
    # The old node object still carries a SUPERSEDES edge that points
    # back at the key — agents can walk the chain of writes.
    assert "k" in old.edges.get(EdgeKind.SUPERSEDES, set())


def test_snapshot_is_jsonable():
    brain = RustBrain()
    brain.remember("a", 1, trust=0.5, tags=("http",))
    brain.remember("b", 2, tags=("auth",), edges={EdgeKind.RELATED_TO: ["a"]})
    payload = brain.snapshot()
    text = json.dumps(payload)
    # HLC tuples are converted to lists in to_dict(), so we need to normalize
    # the payload for comparison (tuples become lists after JSON round-trip)
    normalized = json.loads(text)
    for node in payload:
        node["hlc"] = list(node["hlc"])
    assert normalized == payload


def test_search_orders_newest_first():
    brain = RustBrain()
    brain.remember("a", 1, ts_ns=1000)
    brain.remember("b", 2, ts_ns=3000)
    brain.remember("c", 3, ts_ns=2000)
    keys = [n.key for n in brain.search()]
    assert keys == ["b", "c", "a"]


def test_search_filters_by_trust_and_tag():
    brain = RustBrain()
    brain.remember("a", 1, trust=0.9, tags=("x",))
    brain.remember("b", 2, trust=0.2, tags=("x",))
    brain.remember("c", 3, trust=0.8, tags=("y",))
    out = [n.key for n in brain.search(tag="x", min_trust=0.5)]
    assert out == ["a"]


def test_contains_and_len():
    brain = RustBrain()
    assert "x" not in brain
    brain.remember("x", 1)
    assert "x" in brain
    assert len(brain) == 1


def test_neighbours_walks_edge_kind():
    brain = RustBrain()
    brain.remember("a", 1, edges={EdgeKind.RELATED_TO: ["b", "c"]})
    brain.remember("b", 2)
    brain.remember("c", 3)
    assert sorted(brain.neighbours("a", EdgeKind.RELATED_TO)) == ["b", "c"]
    assert brain.neighbours("a", EdgeKind.CAUSED_BY) == []


def test_bulk_write_is_atomic_per_call():
    brain = RustBrain()
    rows = [
        {"key": f"k{i}", "value": i, "ts_ns": 1000 + i, "trust": 0.5} for i in range(10)
    ]
    n = brain.bulk_write(rows)
    assert n == 10
    assert len(brain) == 10


def test_hive_hermes_backend_roundtrip():
    from hive.rust_brain import HermesBackend

    backend = HermesBackend()
    backend.publish(
        {"key": "endpoint", "value": "/v1/x", "trust": 0.7, "caused_by": ["init"]}
    )
    dump = backend.pull(max_keys=10, min_trust=0.0)
    assert any(d["key"] == "endpoint" for d in dump)
    # JSON-roundtrip safe.
    parsed = json.loads(backend.export_json())
    assert isinstance(parsed, list)


def test_memory_node_to_dict_shape():
    node = MemoryNode(key="k", value=1, ts_ns=42, trust=0.5, tags=("t",))
    d = node.to_dict()
    assert d["key"] == "k"
    assert d["value"] == 1
    assert d["ts_ns"] == 42
    assert d["trust"] == 0.5
    assert d["tags"] == ["t"]
    assert "edges" in d
