"""Tests for deployment markers and blue-green rollout."""

from __future__ import annotations

from hive.deployment import DeploymentMarker


def test_deployment_marker_traffic_weight():
    marker = DeploymentMarker(version="0.4.1", color="green")
    marker.set_traffic_weight(0.25)
    assert marker.traffic_weight == 0.25


def test_deployment_ready_for_promotion():
    marker = DeploymentMarker(version="0.4.1", color="green")
    # Not enough requests yet
    assert not marker.is_ready_for_promotion()
    # Simulate 100 successful requests
    for _ in range(100):
        marker.record_request(success=True)
    assert marker.is_ready_for_promotion()


def test_deployment_not_ready_due_to_errors():
    marker = DeploymentMarker(version="0.4.1", color="green")
    for _ in range(100):
        marker.record_request(success=False)
    assert not marker.is_ready_for_promotion()


def test_deployment_dict_shape():
    marker = DeploymentMarker(version="0.4.1", color="green")
    d = marker.to_dict()
    assert d["version"] == "0.4.1"
    assert d["color"] == "green"
    assert "error_rate" in d
    assert "ready_for_promotion" in d
