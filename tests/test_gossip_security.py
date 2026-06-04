"""Security regression tests for hive.gossip."""

from __future__ import annotations

import os

import pytest

from hive.gossip import GossipProtocol, validate_peer_url
from hive.rust_brain import RustBrain


def test_validate_peer_url_rejects_non_http():
    with pytest.raises(ValueError, match="http or https"):
        validate_peer_url("file:///etc/passwd")


def test_gossip_receive_requires_secret_when_configured():
    brain = RustBrain()
    gossip = GossipProtocol(brain, peers=[])
    os.environ["HIVE_GOSSIP_SECRET"] = "s3cret"
    try:
        with pytest.raises(PermissionError):
            gossip.receive([{"key": "k", "value": 1}], auth_token="bad")
        assert gossip.receive([{"key": "k", "value": 1}], auth_token="s3cret") == 1
    finally:
        os.environ.pop("HIVE_GOSSIP_SECRET", None)


def test_gossip_rejects_file_peer_at_init():
    brain = RustBrain()
    with pytest.raises(ValueError, match="http or https"):
        GossipProtocol(brain, peers=["file:///tmp"])
