"""Regression tests for HLC preservation across snapshot restore and gossip replay."""

from __future__ import annotations

import os
import tempfile

import pytest

from hive.gossip import GossipProtocol
from hive.rust_brain import RustBrain, TimestampRegression


def test_snapshot_restore_preserves_hlc():
    brain = RustBrain(tenant_id="hlc_test")
    node = brain.remember("key1", "value1")
    original_hlc = node.hlc

    with tempfile.NamedTemporaryFile(delete=False, suffix=".gz") as f:
        path = f.name

    try:
        brain.snapshot_to_file(path)
        brain2 = RustBrain(tenant_id="hlc_test")
        brain2.restore_from_file(path)
        restored = brain2.get("key1")
        assert restored is not None
        assert restored.hlc == original_hlc
    finally:
        os.unlink(path)


def test_gossip_receive_preserves_hlc():
    brain = RustBrain()
    source = RustBrain()
    source_node = source.remember("remote", "payload")
    gossip = GossipProtocol(brain, peers=[])

    applied = gossip.receive(
        [
            {
                "key": "remote",
                "value": "payload",
                "trust": 1.0,
                "tags": ["sync"],
                "ts_ns": source_node.ts_ns,
                "hlc": list(source_node.hlc),
            }
        ]
    )
    assert applied == 1
    node = brain.get("remote")
    assert node is not None
    assert node.hlc == source_node.hlc


def test_gossip_replay_does_not_raise_timestamp_regression():
    brain = RustBrain()
    gossip = GossipProtocol(brain, peers=[])
    node = brain.remember("local", "v1")
    events = [
        {
            "key": "local",
            "value": "v1",
            "ts_ns": node.ts_ns,
            "hlc": list(node.hlc),
        }
    ]
    applied = gossip.receive(events)
    assert applied == 1
    assert brain.recall("local") == "v1"


def test_restore_then_write_respects_restored_hlc_order():
    brain = RustBrain()
    n1 = brain.remember("a", 1)
    n2 = brain.remember("b", 2)
    assert n1.hlc < n2.hlc

    with tempfile.NamedTemporaryFile(delete=False, suffix=".gz") as f:
        path = f.name

    try:
        brain.snapshot_to_file(path)
        brain2 = RustBrain()
        brain2.restore_from_file(path)
        with pytest.raises(TimestampRegression):
            brain2.remember("stale", "x", hlc=(0, 0, "stale-node"))
    finally:
        os.unlink(path)


def test_restore_preserves_hlc_and_allows_causal_successor():
    """After restore, writes with causally later HLC must not be rejected."""
    brain = RustBrain(tenant_id="test")
    original_hlc = (5000, 10, "nodeA")
    brain.remember("k1", "v1", hlc=original_hlc)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".gz") as f:
        path = f.name

    try:
        brain.snapshot_to_file(path)

        brain2 = RustBrain(tenant_id="test")
        brain2.restore_from_file(path)
        node = brain2.get("k1")
        assert node is not None
        assert node.hlc == original_hlc

        successor_hlc = (5000, 11, "nodeA")
        brain2.remember("k1", "v2", hlc=successor_hlc)
        updated = brain2.get("k1")
        assert updated is not None
        assert updated.value == "v2"
        assert updated.hlc == successor_hlc
    finally:
        os.unlink(path)


def test_restore_rejects_stale_hlc_after_restore():
    """Writes with HLC earlier than restored state must still raise."""
    brain = RustBrain(tenant_id="test")
    brain.remember("k1", "v1", hlc=(5000, 10, "nodeA"))

    with tempfile.NamedTemporaryFile(delete=False, suffix=".gz") as f:
        path = f.name

    try:
        brain.snapshot_to_file(path)
        brain2 = RustBrain(tenant_id="test")
        brain2.restore_from_file(path)

        with pytest.raises(TimestampRegression):
            brain2.remember("k1", "stale", hlc=(5000, 9, "nodeA"))
    finally:
        os.unlink(path)


def test_bulk_write_preserves_hlc():
    """bulk_write must round-trip HLC from row payloads."""
    brain = RustBrain()
    rows = [
        {"key": "a", "value": "1", "hlc": [1000, 0, "n1"]},
        {"key": "b", "value": "2", "hlc": [1001, 1, "n2"]},
    ]
    brain.bulk_write(rows)

    node_a = brain.get("a")
    node_b = brain.get("b")
    assert node_a is not None
    assert node_b is not None
    assert node_a.hlc == (1000, 0, "n1")
    assert node_b.hlc == (1001, 1, "n2")


def test_gossip_receive_applies_hlc():
    """Gossip receive must apply HLC for causal ordering."""
    brain = RustBrain()
    gossip = GossipProtocol(brain, peers=[])

    applied = gossip.receive(
        [
            {
                "key": "remote",
                "value": "from_peer",
                "hlc": [2000, 0, "peer1"],
            }
        ]
    )
    assert applied == 1
    node = brain.get("remote")
    assert node is not None
    assert node.hlc == (2000, 0, "peer1")


def test_gossip_receive_rejects_stale_update():
    """Gossip with stale HLC must not overwrite newer local state."""
    brain = RustBrain()
    brain.remember("k", "local", hlc=(3000, 5, "local"))

    gossip = GossipProtocol(brain, peers=[])
    applied = gossip.receive(
        [
            {
                "key": "k",
                "value": "stale_peer",
                "hlc": [3000, 3, "peer"],
            }
        ]
    )
    assert applied == 0
    assert brain.recall("k") == "local"


def test_gossip_receive_skips_missing_hlc_on_existing_key():
    """Updates without HLC must not overwrite existing keys."""
    brain = RustBrain()
    brain.remember("k", "original", hlc=(4000, 0, "local"))

    gossip = GossipProtocol(brain, peers=[])
    applied = gossip.receive([{"key": "k", "value": "no_hlc_update"}])
    assert applied == 0
    assert brain.recall("k") == "original"


def test_bulk_write_imports_legacy_ts_ns_rows_into_warm_brain():
    """Pre-HLC rows (ts_ns only) must import without tripping the high-water guard."""
    brain = RustBrain()
    brain.remember("warm", "x")  # a current write raises the high-water mark

    n = brain.bulk_write(
        [
            {"key": "a", "value": 1, "ts_ns": 2000},
            {"key": "b", "value": 2, "ts_ns": 1000},
        ]
    )
    assert n == 2
    assert brain.recall("a") == 1
    assert brain.recall("b") == 2
    # Legacy ordering from ts_ns is preserved: b is older than a.
    assert brain.get("b").hlc < brain.get("a").hlc
