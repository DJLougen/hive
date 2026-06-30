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


def test_gossip_rejects_stale_hlc_overwrite():
    brain = RustBrain()
    gossip = GossipProtocol(brain, peers=[])
    brain.remember("shared", "FRESH", hlc=(2000, 0, "local"))

    applied = gossip.receive(
        [{"key": "shared", "value": "STALE", "hlc": [1000, 0, "remote"]}]
    )
    assert applied == 0
    assert brain.recall("shared") == "FRESH"


def test_gossip_applies_newer_hlc():
    brain = RustBrain()
    gossip = GossipProtocol(brain, peers=[])
    brain.remember("shared", "OLD", hlc=(1000, 0, "local"))

    applied = gossip.receive(
        [{"key": "shared", "value": "NEW", "hlc": [2000, 0, "remote"]}]
    )
    assert applied == 1
    assert brain.recall("shared") == "NEW"


def test_gossip_skips_missing_hlc_on_existing_key():
    brain = RustBrain()
    gossip = GossipProtocol(brain, peers=[])
    brain.remember("k", "local")

    applied = gossip.receive([{"key": "k", "value": "remote"}])
    assert applied == 0
    assert brain.recall("k") == "local"


def test_gossip_allows_missing_hlc_for_new_key():
    brain = RustBrain()
    gossip = GossipProtocol(brain, peers=[])
    applied = gossip.receive([{"key": "new_key", "value": "remote"}])
    assert applied == 1
    assert brain.recall("new_key") == "remote"
