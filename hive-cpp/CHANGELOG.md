# Hive-cpp Changelog

All notable changes to hive-cpp (Rust native backend) will be documented in this file.

## [Unreleased]

### Fixed
- `Router::decide` no longer panics on a malformed model (a non-leaf node
  missing `feature`, `threshold`, or the child a branch needs). The crate is
  built with `panic = "abort"`, so the previous `expect()` calls aborted the
  host Python process on caller-supplied model JSON. A malformed node now
  returns a decision with `action = "escalate"`, `confidence = 0.0`, and a
  `reasoning` string naming the defect.

### Removed
- `CompressionRules.preserve_patterns`: the field was documented but never
  read by `compress_tokens`. Removed rather than left as a silent no-op.
- Unused crate dependencies: `rayon`, `dashmap`, `parking_lot`, `simd-json`,
  `xxhash-rust`. `rand` moved to `[dev-dependencies]` (used only by
  `benches/bench.rs`).

## [0.1.0] - 2026-06-02

### Added

#### Core Rust Implementation (Phase 1 Complete)
- **Router Module** (`src/router.rs`)
  - Decision tree implementation based on busybee-cpu
  - Agent state parsing and feature extraction

- **Compressor Module** (`src/compressor.rs`)
  - Token compression with configurable rules
  - Importance scoring and pattern-based filtering

- **Memory Module** (`src/memory.rs`)
  - Concurrent hash map for agent memory
  - Causal relationship tracking with timestamps
  - Thread-safe insert/retrieve operations

#### Python Integration via PyO3
- **PyO3 Bindings** (`src/lib.rs`)
  - Router: `rust_router_decide()` - JSON serialization interface
  - Compressor: `rust_compress()` - Token compression from Python
  - Memory: `rust_memory_store()` / `rust_memory_retrieve()` - Memory operations
  - All functions handle JSON serialization/deserialization

- **Maturin Build System**
  - `maturin develop` for development builds
  - `maturin build --release` for optimized wheel

#### Testing & Validation
- **Criterion Benchmarks** (`benches/bench.rs`)
  - Microbenchmarks for all three modules
  - Throughput measurements (ops/second)
  - Latency percentiles (p50, p95, p99)

### Performance

No benchmark artifact is committed for this crate, so no latency or speedup
numbers are published here. `cargo bench` runs the criterion suite in
`benches/bench.rs`; record its output before quoting numbers.

### Architecture
```
Python API
    ↓ (JSON serialization)
PyO3 Bindings (src/lib.rs)
    ↓
Rust Native
  ├─ Router (decision tree)
  ├─ Compressor (token compression)
  └─ Memory (concurrent hash map)
```

### Build Requirements
- Rust 1.80+
- Python 3.10+
- maturin for PyO3 builds
- criterion for benchmarking

### Known Limitations
- PyO3 FFI overhead (JSON serialization, boundary crossing) dominates for
  small operations
- Compressor currently includes JSON serialization in timing
- No async support yet (sync-only API)

### Next Steps
1. Add async support via tokio
2. Optimize Compressor to avoid JSON overhead
3. Add more comprehensive error handling
4. Benchmark against production workloads
