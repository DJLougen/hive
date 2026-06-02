"""Rust-Brain: timestamp-protected, graph-structured agent memory.

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
]

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
        ts_ns: Hive-relative nanosecond timestamp. Always set on write.
        trust: Confidence in ``[0, 1]``. Default 1.0; lower = suspect.
        edges: Mapping from edge kind to a set of related node keys.
        tags: Free-form labels for retrieval.
    """

    key: str
    value: Any
    ts_ns: int = field(default_factory=_now_ns)
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

    def __init__(self, *, enforce_monotonic: bool = True) -> None:
        self._nodes: dict[str, MemoryNode] = {}
        self._lock = threading.RLock()
        self._enforce_monotonic = enforce_monotonic
        # Simple per-key counter so we can show "newest first" ordering
        # without re-sorting the whole store on every read.
        self._order: list[str] = []
        self._order_index: dict[str, int] = {}

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
    ) -> MemoryNode:
        """Insert or update a node.

        If ``ts_ns`` is omitted the wall clock is used. If the key already
        exists, the new timestamp MUST be >= the stored one (when monotonic
        enforcement is on) — otherwise :class:`TimestampRegression` is
        raised. The point is to make stale replays loudly fail.
        """
        ts = ts_ns if ts_ns is not None else _now_ns()
        with self._lock:
            existing = self._nodes.get(key)
            if existing is not None and self._enforce_monotonic and ts < existing.ts_ns:
                raise TimestampRegression(
                    f"refusing to write {key!r}: ts={ts} < stored={existing.ts_ns}"
                )
            node = MemoryNode(
                key=key,
                value=value,
                ts_ns=ts,
                trust=max(0.0, min(1.0, trust)),
                tags=set(tags or ()),
            )
            for kind, neighbours in (edges or {}).items():
                for n in neighbours:
                    node.attach(kind, n)
            self._nodes[key] = node
            if key not in self._order_index:
                self._order_index[key] = len(self._order)
                self._order.append(key)
            return node

    def supersede(self, key: str, new_value: Any, **kwargs: Any) -> MemoryNode:
        """Write ``new_value`` for ``key`` and mark the old node as superseded.

        The previous node is captured *before* :meth:`remember` overwrites
        the dictionary slot. The capture is cheap (a reference). The
        monotonic-timestamp invariant is held across the whole operation
        so a concurrent write cannot sneak in a stale ts.
        """
        with self._lock:
            previous = self._nodes.get(key)
            node = self.remember(key, new_value, **kwargs)
        if previous is not None and previous.node_id != node.node_id:
            previous.attach(EdgeKind.SUPERSEDES, key)
        return node
    def forget(self, key: str) -> None:
        with self._lock:
            self._nodes.pop(key, None)
            idx = self._order_index.pop(key, None)
            if idx is not None:
                self._order[idx] = ""

    # -- read path ----------------------------------------------------------

    def recall(self, key: str, default: Any = None) -> Any:
        node = self._nodes.get(key)
        return default if node is None else node.value

    def get(self, key: str) -> MemoryNode | None:
        return self._nodes.get(key)

    def neighbours(self, key: str, kind: str | None = None) -> list[str]:
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

    def search(self, *, tag: str | None = None, min_trust: float = 0.0) -> list[MemoryNode]:
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
        return isinstance(key, str) and key in self._nodes

    def __repr__(self) -> str:
        return f"RustBrain(nodes={len(self._nodes)}, monotonic={self._enforce_monotonic})"


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

    def pull(self, *, max_keys: int = 32, min_trust: float = 0.5) -> list[dict[str, Any]]:
        """Return a Hermes-formatted context dump.

        The output is exactly the list of dicts that should be appended to
        a turn's ``memory`` field before the prompt is built.
        """
        nodes = self.brain.search(min_trust=min_trust)[:max_keys]
        return [n.to_dict() for n in nodes]

    def export_json(self) -> str:
        """Serialise the whole store as a single JSON document."""
        return json.dumps(self.brain.snapshot(), ensure_ascii=False, indent=2)
