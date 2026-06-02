"""Deployment markers for blue-green and canary rollouts.

Provides version markers and traffic-split controls so SRE teams can
safely roll out new Hive versions.

Usage::

    from hive.deployment import DeploymentMarker

    marker = DeploymentMarker(version="0.4.1", color="green")
    marker.set_traffic_weight(0.10)  # 10% traffic
    if marker.is_ready_for_promotion():
        marker.promote()
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DeploymentMarker:
    """Version marker with traffic-split support."""

    version: str
    color: str  # "blue" or "green"
    traffic_weight: float = field(default=0.0)  # 0.0–1.0
    errors: int = field(default=0, repr=False)
    requests: int = field(default=0, repr=False)
    start_time: float = field(default_factory=time.monotonic, repr=False)

    def record_request(self, *, success: bool) -> None:
        self.requests += 1
        if not success:
            self.errors += 1

    def error_rate(self) -> float:
        if self.requests == 0:
            return 0.0
        return self.errors / self.requests

    def set_traffic_weight(self, weight: float) -> None:
        self.traffic_weight = max(0.0, min(1.0, weight))

    def is_ready_for_promotion(self, *, max_error_rate: float = 0.01, min_requests: int = 100) -> bool:
        """Return True if this deployment looks healthy enough to take 100% traffic."""
        if self.requests < min_requests:
            return False
        return self.error_rate() <= max_error_rate

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "color": self.color,
            "traffic_weight": self.traffic_weight,
            "uptime_s": round(time.monotonic() - self.start_time, 1),
            "requests": self.requests,
            "errors": self.errors,
            "error_rate": round(self.error_rate(), 4),
            "ready_for_promotion": self.is_ready_for_promotion(),
        }


__all__ = ["DeploymentMarker"]
