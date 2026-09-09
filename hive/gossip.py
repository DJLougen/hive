"""Cross-node memory gossip for distributed Hive deployments.

Lightweight gossip protocol that syncs memory events between pods so
an agent that hops between nodes does not lose its context.

Usage::

    from hive.gossip import GossipProtocol
    from hive.rust_brain import RustBrain

    brain = RustBrain(tenant_id="org_a")
    gossip = GossipProtocol(brain, peers=["http://hive-2:8080", "http://hive-3:8080"])
    gossip.start()  # background thread

    # After a local remember(), the event is gossiped to peers
    brain.remember("key", "value")
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from typing import Any

_log = logging.getLogger("hive.gossip")


try:
    import urllib.request

    _HAS_HTTP = True
except Exception:  # pragma: no cover
    _HAS_HTTP = False


class GossipProtocol:
    """Fire-and-forget gossip for memory events.

    Parameters
    ----------
    brain:
        The RustBrain to gossip from.
    peers:
        List of peer HTTP endpoints (e.g. ["http://hive-2:8080"]).
    interval:
        Seconds between gossip rounds.
    batch_size:
        Max events per gossip message.
    """

    def __init__(
        self,
        brain: Any,
        *,
        peers: list[str],
        interval: float = 5.0,
        batch_size: int = 100,
    ) -> None:
        self._brain = brain
        self._peers = peers
        self._interval = interval
        self._batch_size = batch_size
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def publish(self, event: dict[str, Any]) -> None:
        """Queue a memory event for gossip."""
        self._queue.put(event)

    def start(self) -> None:
        """Start the background gossip thread."""
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        _log.info("Gossip started with %d peers", len(self._peers))

    def stop(self) -> None:
        """Stop the background thread."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            batch: list[dict[str, Any]] = []
            try:
                for _ in range(self._batch_size):
                    batch.append(self._queue.get(timeout=0.1))
            except queue.Empty:
                pass

            if batch:
                self._gossip_batch(batch)

            self._stop.wait(self._interval)

    def _gossip_batch(self, batch: list[dict[str, Any]]) -> None:
        payload = json.dumps({"events": batch}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        for peer in self._peers:
            try:
                req = urllib.request.Request(
                    f"{peer.rstrip('/')}/gossip/receive",
                    data=payload,
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=2.0) as resp:  # nosec B310
                    if resp.status == 200:
                        _log.debug("Gossiped %d events to %s", len(batch), peer)
            except Exception as exc:
                _log.warning("Gossip to %s failed: %s", peer, exc)

    def receive(self, events: list[dict[str, Any]]) -> int:
        """Receive gossiped events and write them into the local brain."""
        applied = 0
        for ev in events:
            try:
                key = ev["key"]
                raw_hlc = ev.get("hlc")
                if raw_hlc is None:
                    # Without an HLC we cannot establish causal order for updates.
                    if self._brain.get(key) is not None:
                        _log.debug(
                            "Skipping gossip update for %r: missing hlc on existing key",
                            key,
                        )
                        continue
                    hlc = None
                else:
                    hlc = tuple(raw_hlc)
                    self._brain.update_hlc(hlc)

                self._brain.remember(
                    key,
                    ev["value"],
                    trust=ev.get("trust", 1.0),
                    tags=set(ev.get("tags", [])),
                    ts_ns=ev.get("ts_ns"),
                    hlc=hlc,
                )
                applied += 1
            except Exception as exc:
                _log.warning("Failed to apply gossiped event: %s", exc)
        return applied


__all__ = ["GossipProtocol"]
