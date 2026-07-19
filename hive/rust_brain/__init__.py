"""Rust-Brain: timestamp-protected, graph-structured agent memory.

**NOTE: This is a Python reference implementation, not Rust.** The name reflects
the planned production backend (a Rust port in hive-cpp/). This module is the
reference oracle that the Rust core must match. Agents program against this API
today; when the Rust core ships, the same API will be served by a native extension.

A reference implementation in Python that exposes the same data model the
planned Rust core (hive-cpp) will use. The Rust core targets <1µs writes and
<aarch64 NEON / SVE2 vector ops; this Python shim is the ergonomic surface
agents program against today and the reference oracle the Rust port must
match.


Key properties:

* **Timestamp protection** — every write carries a monotonic + wall-clock
  timestamp and an optional trust score. Replaying an older write raises.
* **Graph relations** — nodes carry typed edges (``related_to``, ``caused_by``,
  ``supersedes``, ``attached_to``) so an agent can walk cause/effect chains.
* **Hermes integration** — the ``HermesBackend`` stub matches the payload
  shape expected by the HermesAgent-20 harness; once the Rust core lands the
  same backend talks to a real RPC.
* **Zero mandatory GPU** — pure Python; runs on Jetson Orin/Thor, phones, and
  Raspberry Pi without any CUDA/ROCm.

This file is intentionally self-contained: drop-in usable with only the
standard library plus ``dataclasses``.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

__all__ = [
    "EdgeKind",
    "MemoryNode",
    "RustBrain",
    "HermesBackend",
    "HIVE_EPOCH_NS",
    "HybridLogicalClock",
]

# ---------------------------------------------------------------------------
# Hybrid Logical Clock (HLC)
# ---------------------------------------------------------------------------


class HybridLogicalClock:
    """Hybrid Logical Clock for distributed causal ordering.
    
    Combines wall-clock time with a logical counter to provide:
    - Causal ordering across processes
    - Monotonicity even with NTP corrections or clock skew
    - Unique timestamps for concurrent events
    
    Format: (wall_clock_ns, logical_time, node_id)
    Ordering: wall_clock first, then logical_time, then node_id for tie-breaking
    """
    
    def __init__(self, node_id: str | None = None):
        self.node_id = node_id or uuid.uuid4().hex[:8]
        self._logical_time = 0
        self._last_wall_clock = 0
        self._lock = threading.Lock()
    
    def now(self) -> tuple[int, int, str]:
        """Generate a new timestamp.
        
        Returns:
            Tuple of (wall_clock_ns, logical_time, node_id)
        """
        with self._lock:
            wall_clock = time.time_ns()
            
            # If wall clock went backwards (NTP correction), increment logical time
            if wall_clock <= self._last_wall_clock:
                self._logical_time += 1
            else:
                # Wall clock moved forward, reset logical counter
                self._logical_time = 0
                self._last_wall_clock = wall_clock
            
            return (wall_clock, self._logical_time, self.node_id)
    
    def update(self, received_ts: tuple[int, int, str]) -> None:
        """Update clock based on received timestamp.
        
        Called when receiving a message from another node to ensure
        our clock stays ahead of all observed events.
        """
        with self._lock:
            their_wall, their_logical, _ = received_ts
            our_wall = time.time_ns()
            
            # Take the maximum of our wall clock and theirs
            max_wall = max(our_wall, their_wall)
            
            # If their logical time is >= ours, increment to stay ahead
            if their_logical >= self._logical_time:
                self._logical_time = their_logical + 1
            
            self._last_wall_clock = max_wall


# Global HLC instance for this process
_hlc = HybridLogicalClock()


# Hive epoch: 2024-01-01T00:00:00Z in nanoseconds.
# Using a custom epoch keeps timestamps compact and lets us reason about
# "agent lifetime" rather than Unix time.
HIVE_EPOCH_NS = 1_704_067_200_000_000_000


def _now_ns() -> int:
    """Monotonic+wall nanos since the Hive epoch."""
    return time.time_ns() - HIVE_EPOCH_NS


# ---------------------------------------------------------------------------
# Edge / node model
# ---------------------------------------------------------------------------


class EdgeKind:
    """Edge kinds supported by the graph.

    We use plain string constants rather than an Enum so the wire format
    matches Hermes' JSON schema without custom (de)serialisers.
    """

    RELATED_TO = "related_to"
    CAUSED_BY = "caused_by"
    SUPERSEDES = "supersedes"
    ATTACHED_TO = "attached_to"


@dataclass(slots=True)
class MemoryNode:
    """A single memory entry.

    Attributes:
        key: Stable identifier (e.g. ``"endpoint"`` or ``"session:42"``).
        value: Arbitrary JSON-serialisable payload.
        ts_ns: Hive-relative nanosecond timestamp (wall-clock). Always set on write.
        hlc: Hybrid Logical Clock tuple for causal ordering (logical_time, wall_clock, node_id).
        trust: Confidence in ``[0, 1]``. Default 1.0; lower = suspect.
        edges: Mapping from edge kind to a set of related node keys.
        tags: Free-form labels for retrieval.
    """

    key: str
    value: Any
    ts_ns: int = field(default_factory=_now_ns)
    hlc: tuple[int, int, str] = field(default_factory=lambda: _hlc.now())
    trust: float = 1.0
    edges: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    tags: set[str] = field(default_factory=set)
    node_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def attach(self, kind: str, other_key: str) -> None:
        """Attach an outgoing edge to ``other_key`` of the given kind."""
        self.edges.setdefault(kind, set()).add(other_key)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a Hermes-compatible dict."""
        return {
            "id": self.node_id,
            "key": self.key,
            "value": self.value,
            "ts_ns": self.ts_ns,
            "hlc": list(self.hlc),  # Convert tuple to list for JSON compatibility
            "trust": self.trust,
            "tags": sorted(self.tags),
            "edges": {k: sorted(v) for k, v in self.edges.items()},
        }


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class TimestampRegression(RuntimeError):
    """Raised when a write would move a node backwards in time."""


class RustBrain:
    """Timestamp-protected key→value graph store.

    The ``RustBrain`` is the user-facing entry point. It is safe to use from
    multiple threads (writes are serialised by a single re-entrant lock;
    reads are lock-free and take a snapshot of the index).

    The internal data model — keyed storage + adjacency sets + monotonic
    timestamps — is the exact schema the upcoming Rust core will use, so an
    agent written against this API will not need to change when the Rust
    implementation lands.
    """

    def __init__(self, *, tenant_id: str = "default", tenant_isolation: bool = True, enforce_monotonic: bool = True, default_ttl_s: float | None = None, max_nodes: int = 10_000) -> None:
        self._tenant_id = tenant_id
        self._tenant_isolation = tenant_isolation
        self._nodes: dict[str, MemoryNode] = {}
        self._lock = threading.RLock()
        self._enforce_monotonic = enforce_monotonic
        self._default_ttl_s = default_ttl_s
        self._max_nodes = max_nodes
        # Simple per-key counter so we can show "newest first" ordering
        # without re-sorting the whole store on every read.
        self._order: list[str] = []
        self._order_index: dict[str, int] = {}
    def update_hlc(self, received_hlc: tuple[int, int, str]) -> None:
        """Update the HLC based on a received timestamp from another node.
        
        This ensures causal ordering is maintained across distributed nodes.
        """
        _hlc.update(received_hlc)


    def _remove_order_slot(self, idx: int) -> None:
        """Remove ``_order[idx]`` and shift down indices above ``idx``."""
        del self._order[idx]
        for k, i in list(self._order_index.items()):
            if i > idx:
                self._order_index[k] = i - 1

    def _remove_from_order(self, storage_key: str) -> None:
        """Drop a key from the insertion-order list without leaving tombstones."""
        idx = self._order_index.pop(storage_key, None)
        if idx is not None:
            self._remove_order_slot(idx)

    def _evict_oldest(self) -> None:
        """Evict the oldest entry from the store and keep order indices aligned."""
        if not self._order:
            return
        oldest_key = self._order[0]
        if oldest_key:
            self._nodes.pop(oldest_key, None)
            self._order_index.pop(oldest_key, None)
        self._order.pop(0)
        for k in list(self._order_index):
            self._order_index[k] -= 1

    def _prefix(self, key: str) -> str:
        """Return the internal storage key with tenant prefix when isolation is on."""
        if not self._tenant_isolation or key.startswith(f"{self._tenant_id}:"):
            return key
        return f"{self._tenant_id}:{key}"

    # -- write path ---------------------------------------------------------

    def remember(
        self,
        key: str,
        value: Any,
        *,
        trust: float = 1.0,
        tags: Iterable[str] | None = None,
        edges: Mapping[str, Iterable[str]] | None = None,
        ts_ns: int | None = None,
        hlc: tuple[int, int, str] | None = None,
    ) -> MemoryNode:
        storage_key = self._prefix(key)
        """Insert or update a node.

        If ``ts_ns`` is omitted the wall clock is used. If ``hlc`` is omitted,
        the HLC is used for causal ordering. If the key already exists, the
        new HLC MUST be >= the stored one (when monotonic enforcement is on) —
        otherwise :class:`TimestampRegression` is raised. The point is to make
        stale replays loudly fail.
        """
        ts = ts_ns if ts_ns is not None else _now_ns()
        node_hlc = hlc if hlc is not None else _hlc.now()
        with self._lock:
            existing = self._nodes.get(storage_key)
            if existing is not None and self._enforce_monotonic and node_hlc < existing.hlc:
                raise TimestampRegression(
                    f"refusing to write {storage_key!r}: hlc={node_hlc} < stored={existing.hlc}"
                )
            node = MemoryNode(
                key=key,
                value=value,
                ts_ns=ts,
                hlc=node_hlc,
                trust=max(0.0, min(1.0, trust)),
                tags=set(tags or ()),
            )
            for kind, neighbours in (edges or {}).items():
                for n in neighbours:
                    node.attach(kind, n)
            self._nodes[storage_key] = node
            if storage_key not in self._order_index:
                self._order_index[storage_key] = len(self._order)
                self._order.append(storage_key)
            # Evict oldest entries if over capacity
            while len(self._nodes) > self._max_nodes:
                self._evict_oldest()
            return node

    def supersede(self, key: str, new_value: Any, **kwargs: Any) -> MemoryNode:
        """Write ``new_value`` for ``key`` and mark the old node as superseded.

        The previous node is captured *before* :meth:`remember` overwrites
        the dictionary slot. The capture is cheap (a reference). The
        monotonic-timestamp invariant is held across the whole operation
        so a concurrent write cannot sneak in a stale ts.
        """
        storage_key = self._prefix(key)
        with self._lock:
            previous = self._nodes.get(storage_key)
            node = self.remember(key, new_value, **kwargs)
        if previous is not None and previous.node_id != node.node_id:
            previous.attach(EdgeKind.SUPERSEDES, key)
        return node

    def forget(self, key: str) -> None:
        key = self._prefix(key)
        with self._lock:
            self._nodes.pop(key, None)
            self._remove_from_order(key)

    # -- read path ----------------------------------------------------------

    def recall(self, key: str, default: Any = None) -> Any:
        key = self._prefix(key)
        node = self._nodes.get(key)
        return default if node is None else node.value

    def get(self, key: str) -> MemoryNode | None:
        return self._nodes.get(self._prefix(key))

    def neighbours(self, key: str, kind: str | None = None) -> list[str]:
        key = self._prefix(key)
        """Return keys reachable from ``key`` via ``kind`` edges (any kind if
        ``None``)."""
        node = self._nodes.get(key)
        if node is None:
            return []
        if kind is None:
            out: set[str] = set()
            for neighbours in node.edges.values():
                out.update(neighbours)
            return sorted(out)
        return sorted(node.edges.get(kind, ()))

    def search(
        self, *, tag: str | None = None, min_trust: float = 0.0
    ) -> list[MemoryNode]:
        """Linear scan over the store. Fine up to ~10k nodes; the Rust core
        will use a roaring bitmap index for the same query."""
        with self._lock:
            snapshot = list(self._nodes.values())
        out: list[MemoryNode] = []
        for node in snapshot:
            if node.trust < min_trust:
                continue
            if tag is not None and tag not in node.tags:
                continue
            out.append(node)
        # Newest first.
        out.sort(key=lambda n: n.ts_ns, reverse=True)
        return out

    def snapshot(self) -> list[dict[str, Any]]:
        """Return a Hermes-compatible JSON dump of the entire store."""
        with self._lock:
            return [self._nodes[k].to_dict() for k in self._order if k]

    # -- bulk ---------------------------------------------------------------

    def bulk_write(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """Apply many writes at once. Returns the number of writes applied."""
        n = 0
        for row in rows:
            self.remember(
                key=row["key"],
                value=row.get("value"),
                trust=row.get("trust", 1.0),
                tags=row.get("tags", ()),
                edges=row.get("edges"),
                ts_ns=row.get("ts_ns"),
                hlc=tuple(row["hlc"]) if row.get("hlc") is not None else None,
            )
            n += 1
        return n

    # -- introspection ------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        with self._lock:
            n = len(self._nodes)
            ts = sorted(n.ts_ns for n in self._nodes.values())
        return {
            "node_count": n,
            "oldest_ts_ns": ts[0] if ts else None,
            "newest_ts_ns": ts[-1] if ts else None,
            "edge_kinds": sorted({k for n in self._nodes.values() for k in n.edges}),
        }

    def __len__(self) -> int:
        return len(self._nodes)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and self._prefix(key) in self._nodes

    def expire(self, key: str, *, ttl_s: float | None = None) -> bool:
        """Remove a key if it has exceeded its TTL. Returns True if removed."""
        ttl = ttl_s if ttl_s is not None else self._default_ttl_s
        if ttl is None:
            return False
        storage_key = self._prefix(key)
        node = self._nodes.get(storage_key)
        if node is None:
            return False
        age_s = (_now_ns() - node.ts_ns) / 1e9
        if age_s > ttl:
            self.forget(key)
            return True
        return False

    def gc_expired(self) -> int:
        """Remove all expired entries. Returns count removed."""
        if self._default_ttl_s is None:
            return 0
        removed = 0
        for storage_key in list(self._nodes):
            node = self._nodes[storage_key]
            age_s = (_now_ns() - node.ts_ns) / 1e9
            if age_s > self._default_ttl_s:
                self._nodes.pop(storage_key, None)
                self._remove_from_order(storage_key)
                removed += 1
        return removed


    def revoke_tenant(self, tenant_id: str | None = None) -> int:
        """Remove ALL data belonging to a tenant (GDPR Article 17 / offboarding).

        Returns the number of keys removed.
        """
        target = tenant_id or self._tenant_id
        prefix = f"{target}:"
        with self._lock:
            to_remove = [k for k in self._nodes if k.startswith(prefix)]
            for k in to_remove:
                self._nodes.pop(k, None)
                self._remove_from_order(k)
            return len(to_remove)

    def snapshot_to_file(self, path: str) -> dict[str, Any]:
        """Persist a compressed snapshot to disk. Returns metadata dict.

        The snapshot is a gzip-compressed JSON file with an embedded SHA-256
        checksum that :meth:`restore_from_file` verifies for corruption/tamper
        detection.
        """
        import gzip

        nodes = self.snapshot()
        # Checksum covers the canonical node payload and is embedded in the file
        # (not just returned) so restore_from_file can actually verify it.
        nodes_json = json.dumps(nodes, sort_keys=True, ensure_ascii=False)
        checksum = hashlib.sha256(nodes_json.encode("utf-8")).hexdigest()
        data = {
            "tenant_id": self._tenant_id,
            "tenant_isolation": self._tenant_isolation,
            "nodes": nodes,
            "sha256": checksum,
            "version": "hive-snapshot-v1",
        }
        payload = json.dumps(data).encode("utf-8")
        compressed = gzip.compress(payload)
        with open(path, "wb") as fh:
            fh.write(compressed)
        return {"path": path, "node_count": len(nodes), "sha256": checksum}

    def restore_from_file(self, path: str) -> int:
        """Restore from a snapshot file. Returns number of nodes restored."""
        import gzip

        with open(path, "rb") as fh:
            compressed = fh.read()
        payload = gzip.decompress(compressed)
        data = json.loads(payload.decode("utf-8"))
        if data.get("version") != "hive-snapshot-v1":
            raise ValueError(f"Unsupported snapshot version: {data.get('version')}")
        nodes = data.get("nodes", [])
        # Verify integrity before mutating state. Snapshots written before the
        # checksum was embedded omit "sha256"; those skip verification.
        expected = data.get("sha256")
        if expected is not None:
            actual = hashlib.sha256(
                json.dumps(nodes, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            if actual != expected:
                raise ValueError(
                    "snapshot checksum mismatch: file is corrupt or tampered"
                )
        self._nodes.clear()
        self._order.clear()
        self._order_index.clear()
        for node_dict in nodes:
            hlc_raw = node_dict.get("hlc")
            hlc = tuple(hlc_raw) if hlc_raw is not None else _hlc.now()
            node = MemoryNode(
                key=node_dict["key"],
                value=node_dict["value"],
                ts_ns=node_dict["ts_ns"],
                hlc=hlc,
                trust=node_dict.get("trust", 1.0),
                tags=set(node_dict.get("tags", [])),
                node_id=node_dict.get("id", uuid.uuid4().hex[:12]),
            )
            for kind, neighbours in node_dict.get("edges", {}).items():
                for n in neighbours:
                    node.attach(kind, n)
            storage_key = self._prefix(node.key)
            self._nodes[storage_key] = node
            self._order_index[storage_key] = len(self._order)
            self._order.append(storage_key)
            _hlc.update(hlc)
        return len(nodes)

    def __repr__(self) -> str:
        return (
            f"RustBrain(tenant={self._tenant_id!r}, nodes={len(self._nodes)}, monotonic={self._enforce_monotonic})"
        )


# ---------------------------------------------------------------------------
# Hermes backend
# ---------------------------------------------------------------------------


class HermesBackend:
    """Bidirectional bridge between :class:`RustBrain` and HermesAgent-20.

    The agent harness calls ``publish`` to push new memories into the store
    and ``pull`` to fetch a context window for a turn. The shape of the
    payloads mirrors the JSON schema Hermes uses for ``memory.*`` events so
    no glue is required once the Rust core replaces this implementation.
    """

    def __init__(self, brain: RustBrain | None = None) -> None:
        self.brain = brain or RustBrain()

    def publish(self, event: Mapping[str, Any]) -> MemoryNode:
        """Translate a Hermes memory event into a brain write.

        Expected event keys: ``key``, ``value``, ``trust`` (optional),
        ``tags`` (optional), ``caused_by`` (optional list of keys).
        """
        key = str(event["key"])
        edges: dict[str, list[str]] = {}
        caused_by = event.get("caused_by") or ()
        if caused_by:
            edges[EdgeKind.CAUSED_BY] = list(caused_by)
        return self.brain.remember(
            key=key,
            value=event.get("value"),
            trust=float(event.get("trust", 1.0)),
            tags=event.get("tags") or (),
            edges=edges or None,
        )

    def pull(
        self, *, max_keys: int = 32, min_trust: float = 0.5
    ) -> list[dict[str, Any]]:
        """Return a Hermes-formatted context dump.

        The output is exactly the list of dicts that should be appended to
        a turn's ``memory`` field before the prompt is built.
        """
        nodes = self.brain.search(min_trust=min_trust)[:max_keys]
        return [n.to_dict() for n in nodes]

    def export_json(self) -> str:
        """Serialise the whole store as a single JSON document."""
        return json.dumps(self.brain.snapshot(), ensure_ascii=False, indent=2)
