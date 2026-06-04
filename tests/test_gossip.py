"""Tests for cross-node gossip."""

from __future__ import annotations

from hive.gossip import GossipProtocol
from hive.rust_brain import RustBrain


def test_gossip_publish_and_receive():
    brain = RustBrain()
    gossip = GossipProtocol(brain, peers=[])
    gossip.publish({"key": "k1", "value": "v1", "trust": 0.9})
    # No crash on publish


def test_gossip_receive_applies_events():
    brain = RustBrain()
    gossip = GossipProtocol(brain, peers=[])
    events = [
        {"key": "k1", "value": "v1", "trust": 0.9, "tags": ["a"]},
        {"key": "k2", "value": "v2", "trust": 0.8},
    ]
    applied = gossip.receive(events)
    assert applied == 2
    assert brain.recall("k1") == "v1"
    assert brain.recall("k2") == "v2"


def test_gossip_start_stop():
    brain = RustBrain()
    gossip = GossipProtocol(brain, peers=[], interval=0.1)
    gossip.start()
    gossip.stop()
    # No crash
