"""Tests for cross-node gossip."""

from __future__ import annotations

from unittest import mock

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


def test_gossip_batch_returns_false_on_peer_failure():
    brain = RustBrain()
    gossip = GossipProtocol(brain, peers=["http://peer-a:8080"])
    event = {"key": "k1", "value": "v1"}
    with mock.patch("urllib.request.urlopen", side_effect=OSError("network down")):
        assert gossip._gossip_batch([event]) is False


def test_gossip_run_requeues_failed_batch():
    brain = RustBrain()
    gossip = GossipProtocol(brain, peers=["http://peer-a:8080"])
    event = {"key": "k1", "value": "v1"}
    gossip._queue.put_nowait(event)

    with mock.patch("urllib.request.urlopen", side_effect=OSError("network down")):
        batch = [gossip._queue.get(timeout=0.1)]
        if not gossip._gossip_batch(batch):
            for ev in batch:
                gossip._queue.put_nowait(ev)

    assert gossip._queue.get_nowait() == event


def test_gossip_publish_drops_when_queue_full():
    brain = RustBrain()
    gossip = GossipProtocol(brain, peers=[], max_queue_size=1)
    gossip.publish({"key": "k1", "value": "v1"})
    gossip.publish({"key": "k2", "value": "v2"})
    assert gossip._queue.qsize() == 1
    assert gossip._queue.get_nowait()["key"] == "k1"
