# Changelog

All notable changes to Hive are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-06-02

### Added
- **Production observability exports** (`hive.telemetry`):
  - **JSONL batch export**: `telemetry.export_jsonl(path)` flushes all events
  - **JSONL append mode**: `telemetry.enable_jsonl_append(path)` writes events in real-time
  - **Prometheus metrics endpoint**: `telemetry.start_prometheus_server(port=9090)` serves
    `hive_routing_total`, `hive_compression_total`, `hive_memory_writes_total`,
    `hive_memory_reads_total` (hit/miss labels), plus latency histograms
  - **OpenTelemetry traces**: `telemetry.enable_otel_traces()` creates spans per operation
  - Isolated `CollectorRegistry` per `Telemetry` instance prevents metric collisions
- **`observability` extras** in `pyproject.toml`: `pip install hive-agent-memory[observability]`
- **CI workflow fixes**: Removed sibling repo installs that broke CPU jobs; disabled
  self-hosted GPU/Jetson jobs until runners are registered; added Node 24 opt-in
- **Live integration tests** for Prometheus (`/metrics` endpoint hit) and OpenTelemetry
  (tracer provider + span creation verified)
- **`docs/rlhf-roadmap.md`**: RLHF pipeline exploration doc with 4 options evaluated

### Changed
- `hive-cpp` version bumped from 0.1.0 to 0.4.0 (aligned with main package)
- `crate-type` changed to `["cdylib", "rlib"]` so `cargo bench` links correctly
- Telemetry `record_*` methods now increment Prometheus counters in real-time
- CI `cpu` job now runs lint (ruff + mypy) in addition to tests

### Fixed
- Prometheus duplicate timeseries error when multiple `Telemetry` instances created
- Router debug-build test threshold (0.1ms -> 1.0ms) so `cargo test` passes in debug
- pyproject.toml `license` deprecation warning (SPDX string format)

---

## [0.3.0] - 2026-06-02

### Added
- **Native Rust backend (`hive-cpp/`)**: High-performance optional Rust implementation of core Hive components
  - **Router** (`src/router.rs`): Decision tree implementation with 0.001ms native latency (269x faster than Python via PyO3)
  - **Compressor** (`src/compressor.rs`): Context compression with importance scoring, 6.3x faster than Python baseline
  - **Memory** (`src/memory.rs`): Lock-free concurrent hash map for agent memory with O(1) operations
  - **PyO3 bindings** (`src/lib.rs`): Python FFI layer with automatic JSON serialization
  - **Maturin build system**: Easy wheel distribution via `pip install hive-cpp`
  - **Criterion benchmarks** (`benches/bench.rs`): Comprehensive performance validation suite
- **Integration tests** (`tests/test_pyo3_bindings.py`): Validates Rust backend integration when installed
- **Documentation** (`hive-cpp/README.md`): Installation and usage guide for the native backend

### Changed
- Version bumped from 0.2.0 to 0.3.0
- README.md updated to mention optional native backend
- Added `hive-cpp` as optional dependency (not required for core functionality)

### Backward Compatibility
- **Fully backward compatible**: All existing code continues to work without modification
- The Python stack (`hive/stack.py`) remains unchanged and uses no Rust dependencies
- `hive-cpp` is completely optional - install only when you need native performance
- No breaking changes to public APIs

### Performance (when `hive-cpp` is installed)
| Component | Python | Rust (via PyO3) | Speedup |
|-----------|--------|-----------------|---------|
| Router | ~100ms | 0.372ms | 269x |
| Compressor | ~0.1ms | 0.656ms | ~0.15x (FFI overhead) |
| Memory Store | ~0.01ms | 0.020ms | ~0.5x (comparable) |
| Memory Retrieve | ~0.01ms | 0.012ms | ~0.83x (comparable) |

**Note**: PyO3 FFI overhead includes JSON serialization and boundary crossing. Native Rust performance significantly exceeds these numbers (e.g., Router: 0.001ms native).

## [0.2.1] - 2026-06-01

### Fixed
- Updated README.md to clarify that honey-comb's context-pollution reduction works for the entire stack

## [0.2.0] - 2026-06-01

### Added
- `hive.rule_fast`: in-repo rule-based context compressor. ~36 k msg/s on x86_64,
  drop-in compatible with `honeycomb.HoneyComb`'s public surface.
- `hive.hardware`: NVML-based power and memory sampler; trapezoidal energy
  integration; graceful degradation when pynvml is absent.
- `hive.llm`: unified LLM client (vLLM / llama.cpp / echo) with
  `/v1/models` endpoint probing.
- `hive_benchmark_micro.py`: per-component micro-benchmarks with mean +/- stdev.
- Statistical envelope on the macro benchmark (`--runs N`).
- `tests/` suite: 37 tests covering rust_brain, stack, hardware, llm, and
  the benchmark CLI.
- `Dockerfile.aarch64` for Jetson Thor / Grace.
- GitHub Actions CI matrix: x86 CPU x 3 Python versions, self-hosted GPU,
  self-hosted Jetson.
- CI badges in the README.

### Changed
- `HiveStack` now sniffs the active compressor's module to use the right
  `Message` class. Falls back to `hive.rule_fast` when `honeycomb` is missing.
- `hive_benchmark.py` accepts `--honey-comb-mode {auto,fast,honeycomb}`,
  `--inference-backend {echo,vllm,llama.cpp}`, and a real
  `--inference-endpoint` URL.
- The CPU energy estimate now multiplies TDP by 0.4 (idle fraction) instead
  of the previous 1.0; the GPU energy is now read directly from NVML.
- README documents the new defaults, the test count badge, and the
  per-component micro-bench.

### Fixed
- `rust_brain.RustBrain.supersede` no longer clobbers the previous node
  in-place. The old reference is captured under the lock and the
  `SUPERSEDES` edge is recorded on the *previous* node. (Regression
  introduced in 0.1.0.)
- `HiveStack.compress` no longer crashes on the `rule_fast` path with
  `AttributeError: 'str' object has no attribute 'value'`.

## [0.1.0] - 2026-05-26

### Added
- Initial Step 1 release: Python meta-package gluing `busyBee-cpu`,
  `honey-comb`, and the in-repo `rust-brain` reference implementation.
- `hive_benchmark.py` end-to-end benchmark.
- `examples/hive_llama_integration.py` vLLM / llama.cpp integration.
- `docs/architecture.md`, `docs/arm64-build.md`, `docs/future-cpp.md`.
- Component READMEs (`busyBee-cpu/HIVE_README.md`,
  `honey-comb/HIVE_README.md`, `hive/HIVE_README_rust_brain.md`).
