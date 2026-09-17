# hive/rust_brain — Python Reference Implementation

> **This is a Python module, not Rust.** The name `rust_brain` reflects the
> planned production backend: a Rust port living in [`hive-cpp/`](../../hive-cpp/).
> This Python shim is the **reference oracle** that the Rust core must match
> byte-for-byte. Agents program against this API today; when the Rust core
> ships, the same API will be served by a native extension with no caller changes.

## Why the name?

The module implements the data model (timestamped graph store with typed edges)
that the Rust core will implement. The Python version is:

- The **specification** — the Rust port must produce identical results
- The **test oracle** — CI compares Rust outputs against Python reference
- The **development surface** — agents use this today, Rust port is opt-in

See [`docs/component-rust_brain.md`](../../docs/component-rust_brain.md) for the
full positioning and [`docs/future-cpp.md`](../../docs/future-cpp.md) for the
Rust port plan.

## Usage

```python
from hive.rust_brain import RustBrain, EdgeKind

brain = RustBrain()
brain.remember("bug_A", {"type": "race condition"})
brain.remember("fix_B", {"type": "mutex"}, edges={"caused_by": ["bug_A"]})
```

## Performance

Measured at ~177K writes/sec in `docs/benchmarks/latest-micro.json` (DGX Spark, Python
implementation, synthetic load). The optional `hive-cpp` crate provides native `route`/`compress`
paths; set `HIVE_BACKEND=native` to use them once it is installed. Memory still uses this Python
`RustBrain` store.
