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
    token:
        Optional shared secret. When set, outbound batches carry an
        ``Authorization: Bearer <token>`` header and :meth:`receive` must
        be called with the matching token (or ``None`` to stay open for
        local/dev use — no authentication is performed then).
    """

    def __init__(
        self,
        brain: Any,
        *,
        peers: list[str],
        interval: float = 5.0,
        batch_size: int = 100,
        token: str | None = None,
        max_queue_size: int = 10_000,
    ) -> None:
        self._brain = brain
        self._peers = peers
        self._interval = interval
        self._batch_size = batch_size
        self._token = token
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=max_queue_size)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def publish(self, event: dict[str, Any]) -> None:
        """Queue a memory event for gossip.

        For full-fidelity replication pass ``node.to_dict()`` — peers then
        preserve ts_ns/hlc/edges. Minimal ``{"key", "value"}`` events work
        too (fresh timestamps are assigned on receipt).
        """
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            _log.warning(
                "Gossip queue full; dropping event for key %s", event.get("key")
            )

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
                if not self._gossip_batch(batch):
                    for event in batch:
                        try:
                            self._queue.put_nowait(event)
                        except queue.Full:
                            _log.error(
                                "Gossip queue full; dropping event for key %s after send failure",
                                event.get("key"),
                            )

            self._stop.wait(self._interval)

    def _gossip_batch(self, batch: list[dict[str, Any]]) -> bool:
        if not self._peers:
            return True
        payload = json.dumps({"events": batch}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._token is not None:
            headers["Authorization"] = f"Bearer {self._token}"
        delivered = False
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
                        delivered = True
                        _log.debug("Gossiped %d events to %s", len(batch), peer)
            except Exception as exc:
                _log.warning("Gossip to %s failed: %s", peer, exc)
        return delivered

    def receive(
        self, events: list[dict[str, Any]], *, token: str | None = None
    ) -> int:
        """Receive gossiped events and write them into the local brain.

        Propagates trust, tags, causal edges (``caused_by`` lists plus full
        ``edges`` maps), ``ts_ns`` and ``hlc`` when present, and advances
        the local HLC so ordering is preserved across nodes. When the
        protocol was configured with a ``token``, callers must supply the
        matching token or a :class:`PermissionError` is raised.
        """
        if self._token is not None and token != self._token:
            raise PermissionError("gossip token mismatch")
        applied = 0
        for ev in events:
            try:
                if "key" not in ev:
                    raise ValueError("gossip event missing 'key'")
                key = ev["key"]
                edges: dict[str, list[str]] = {}
                if isinstance(ev.get("edges"), dict):
                    for kind, targets in ev["edges"].items():
                        edges[str(kind)] = [str(t) for t in targets]
                caused_by = ev.get("caused_by")
                if caused_by:
                    edges.setdefault("caused_by", []).extend(
                        str(k) for k in caused_by
                    )
                raw_hlc = ev.get("hlc")
                if raw_hlc is None:
                    # Without an HLC we cannot establish causal order for updates.
                    if self._brain.get(key) is not None:
                        _log.debug(
                            "Skipping gossip update for %r: missing hlc on existing key",
                            key,
                        )
                        continue
                    node_hlc = None
                else:
                    node_hlc = tuple(raw_hlc)
                    if hasattr(self._brain, "update_hlc"):
                        self._brain.update_hlc(node_hlc)

                self._brain.remember(
                    key,
                    ev.get("value"),
                    trust=ev.get("trust", 1.0),
                    tags=set(ev.get("tags", [])),
                    edges=edges or None,
                    ts_ns=ev.get("ts_ns"),
                    hlc=node_hlc,
                )
                applied += 1
            except Exception as exc:
                _log.warning("Failed to apply gossiped event: %s", exc)
        return applied


__all__ = ["GossipProtocol"]
