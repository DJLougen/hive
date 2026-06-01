"""Quick smoke test for the Hive stack without busyBee / honey-comb deps.

This module exercises ``hive.rust_brain`` and ``hive.stack`` in isolation,
so it works in the Step 1 package even before the sibling packages are
installed.
"""

from __future__ import annotations

from hive.rust_brain import EdgeKind, MemoryNode, RustBrain


def smoke() -> dict[str, object]:
    brain = RustBrain()
    a = brain.remember("endpoint", "/v1/chat", tags=("http",), trust=0.9, ts_ns=1000)
    b = brain.supersede("endpoint", "/v2/chat", trust=0.95, ts_ns=2000)
    a.attach(EdgeKind.RELATED_TO, "session:42")
    return {
        "node_count": len(brain),
        "newest_ts_ns": b.ts_ns,
        "neighbours": brain.neighbours("endpoint"),
        "snapshot_first": brain.snapshot()[0],
    }


if __name__ == "__main__":
    import json
    print(json.dumps(smoke(), indent=2, default=str))
