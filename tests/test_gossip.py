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


def test_gossip_queue_is_bounded_and_counts_drops():
    """An unstarted protocol must not grow without bound (or block callers)."""
    brain = RustBrain()
    gossip = GossipProtocol(brain, peers=[], interval=60.0, batch_size=1)
    assert gossip.stats() == {"published": 0, "dropped": 0, "queued": 0}

    for i in range(11):
        gossip.publish({"key": f"k{i}", "value": i})

    assert gossip.stats()["published"] == 10  # batch_size * 10
    assert gossip.stats()["dropped"] == 1
    assert gossip.stats()["queued"] == 10


def test_gossip_stays_publishable_after_drops():
    brain = RustBrain()
    gossip = GossipProtocol(brain, peers=[], interval=60.0, batch_size=1)
    for i in range(12):
        gossip.publish({"key": f"k{i}", "value": i})

    assert gossip.stats()["dropped"] == 2
    # A drop must not poison later publishes.
    gossip._queue.get_nowait()
    gossip.publish({"key": "later", "value": 1})
    assert gossip.stats()["published"] == 11
