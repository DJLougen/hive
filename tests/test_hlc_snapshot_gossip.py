"""Tests for HLC preservation across snapshot restore and gossip replay."""

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
