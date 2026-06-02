"""Kubernetes-style health and readiness probes for Hive.

Provides HTTP endpoints that orchestrators (K8s, Docker Compose, load
balancers) can hit to decide whether a Hive instance is alive and
ready to serve traffic.

Usage::

    from hive.health import HealthServer
    server = HealthServer(stack=my_stack, port=8080)
    server.start_in_background()

    # Probe endpoints:
    #   GET /health  → 200 {status: "healthy", uptime_s: 42.0}
    #   GET /ready   → 200 if all backends OK, 503 otherwise
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


@dataclass
class HealthStatus:
    """Snapshot of Hive health."""

    status: str = "healthy"
    uptime_s: float = field(default_factory=lambda: 0.0)
    backends: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {
                "status": self.status,
                "uptime_s": round(self.uptime_s, 3),
                "backends": self.backends,
            },
            indent=2,
        )


class _HealthHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler for /health and /ready."""

    # Shared state injected by HealthServer
    stack_ref: Any | None = None
    start_time: float = 0.0

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress default logging — too noisy for probes
        pass

    def do_GET(self) -> None:
        uptime = time.perf_counter() - self.start_time
        status = HealthStatus(
            status="healthy",
            uptime_s=uptime,
            backends=self._check_backends(),
        )

        if self.path == "/health":
            code = 200
        elif self.path == "/ready":
            code = 200 if all(v in ("ok", "degraded") for v in status.backends.values()) else 503
        else:
            code = 404

        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(status.to_json().encode("utf-8"))

    def _check_backends(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.stack_ref is None:
            out["hive"] = "unknown"
            return out
        try:
            # RustBrain is alive if we can read its stats
            _ = self.stack_ref.brain.stats()
            out["rust_brain"] = "ok"
        except Exception:
            out["rust_brain"] = "down"

        # Compressor is alive if it has a process method
        out["compressor"] = "ok" if hasattr(self.stack_ref.comb, "process") else "missing"

        # Policy is optional — mark ok if present, degraded if absent
        out["policy"] = "ok" if self.stack_ref.busybee is not None else "degraded"

        return out


class HealthServer:
    """Lightweight HTTP server for K8s probes.

    Parameters
    ----------
    stack:
        The :class:`HiveStack` instance to monitor.
    port:
        TCP port to listen on.
    """

    def __init__(self, stack: Any, *, port: int = 8080) -> None:
        self.stack = stack
        self.port = port
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start_in_background(self) -> None:
        """Start the server in a daemon thread."""
        _HealthHandler.stack_ref = self.stack
        _HealthHandler.start_time = time.perf_counter()

        self._server = HTTPServer(("0.0.0.0", self.port), _HealthHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Shut down the server."""
        if self._server is not None:
            self._server.shutdown()
            self._server = None

    def __enter__(self) -> "HealthServer":
        self.start_in_background()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()


def is_healthy(stack: Any) -> tuple[bool, dict[str, str]]:
    """Quick synchronous health check without starting a server.

    Returns ``(is_ready, backend_status)``.
    """
    try:
        _ = stack.brain.stats()
        brain = "ok"
    except Exception:
        brain = "down"

    compressor = "ok" if hasattr(stack.comb, "process") else "missing"
    policy = "ok" if stack.busybee is not None else "degraded"

    backends = {"rust_brain": brain, "compressor": compressor, "policy": policy}
    # Policy is optional — degraded is acceptable; only down/missing fails
    ready = all(v in ("ok", "degraded") for v in backends.values())
    return ready, backends


__all__ = ["HealthServer", "HealthStatus", "is_healthy"]
