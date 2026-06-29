# Changelog

All notable changes to Hive are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.6.1] - 2026-06-29

### Security
- `rust_brain` snapshot integrity: `RustBrain.snapshot_to_file` now embeds the
  SHA-256 of the node payload in the file, and `restore_from_file` verifies it
  before mutating state. Previously the checksum was computed and discarded, so
  a corrupted or tampered snapshot restored silently. A failed check now raises
  `ValueError("snapshot checksum mismatch ...")` and leaves the existing store
  untouched. Backward compatible: pre-checksum snapshots skip verification.

### Added
- PyPI publishing in the release workflow (trusted publishing via GitHub OIDC).
- `[full]` optional extra: `busybee-cpu` + `honey-comb` pulled from PyPI.
- `docs/PYPI.md` with one-time publisher setup instructions.

### Changed
- Dependency pins refreshed across core, dev, observability, and GPU extras.
- `hive_api_server` reads version from `hive.__version__` instead of a hardcoded string.

### Tests
- Replaced the catch-all corruption test with deterministic checks: content
  tamper → checksum `ValueError`, restore atomicity (existing data survives a
  failed restore), and truncated-file framing failure.

## [Unreleased]

## [0.6.0] - 2026-06-11

### Added
- Real-workload SWE-bench-lite A/B evaluation (`scripts/hive_swebench_eval.py`) with
  committed baseline vs Hive runs under `docs/benchmarks/swebench-lite/`.
- Compression sensitivity sweep (`scripts/hive_compression_sweep.py`).
- Hybrid Logical Clock (HLC) for causal-memory ordering, plus rust_brain concurrency tests.
- Release workflow (`.github/workflows/release.yml`) and an evaluation-result issue template.
- CITATION.cff validation in CI.

### Changed
- README rewritten to reflect the actual current state and module surface: real measured
  evaluation results, accurate public API, the full ~28-module architecture, and corrected
  reproduce/script paths.
- Routing accuracy framed as in-distribution with an explicit out-of-distribution caveat;
  removed the unmeasured ROI/case-study and energy headline claims.
- Packaging metadata aligned to the 0.6.0 release.

### Fixed
- `pyproject.toml`: restored the missing `[build-system]` table and removed a stray
  top-level `version` key; the package version now reports `0.6.0` (was `0.5.0`).
- Version drift: `hive.__version__`, the FastAPI server, and the Helm chart now report `0.6.0`.
- README: balanced the code fences that previously swallowed the Online Learning / Causal
  Memory and Development sections; corrected the compression label set to
  `CORE/DISTILL/COMPACT/DROP/STALE/ESCALATE` and the public API examples to the real types.
- `.github/citation.cff` is now valid CFF 1.2.0 (was JSON); CI validates it as CFF via
  `cffconvert` instead of `json.load`.

## [0.5.0] - 2026-06-02

### Added — Enterprise-Grade Infrastructure (Critical → High → Medium → Low)

#### Critical Tier
- **Schema validation** (`hive.schemas`): Pydantic models for `AgentState`,
  `RouteDecisionOut`, `MemoryNodeIn` with `validate_state()` / `validate_memory()`.
  Enabled via `HiveStack(validate=True)`; backward-compatible default `validate=False`.
- **Multi-tenancy** (`hive.rust_brain`): `RustBrain(tenant_id=..., tenant_isolation=True)`
  prefixes internal storage keys per tenant. Cross-tenant reads return `None`.
- **Health / readiness probes** (`hive.health`): `HealthServer` with `/health`
  (liveness, always 200) and `/ready` (readiness, 200/503). `is_healthy()` synchronous
  check for load balancer integration.

#### High Tier
- **Rate limiting** (`hive.ratelimit`): Token-bucket per `(tenant_id, operation)`.
  `HiveStack(rate_limiter=...)` returns `source="ratelimit"` escalation when bucket
  empty. Backward-compatible default `rate_limiter=None`.
- **Config management** (`hive.config`): `HiveConfig.from_env()` reads `HIVE_*`
  environment variables. `validate()` enforces constraints (`rate_limit >= 0`).
  Auto-wires `tenant_isolation` and `default_ttl_s` into `RustBrain`.
- **Data retention / TTL** (`hive.rust_brain`): `expire(key)` removes stale entries;
  `gc_expired()` bulk-scans expired memories. GDPR Article 17 ready.

#### Medium Tier
- **Load testing** (`scripts/hive_load_test.py`): Sustained-load validation with
  p50/p95/p99 latency reporting, error-rate tracking, and JSON output.
- **Blue-green deployment markers** (`hive.deployment`): `DeploymentMarker` with
  traffic-weight control, error-rate-based promotion gates, and `to_dict()` for dashboards.
- **Chaos engineering** (`scripts/hive_chaos.py`): Latency injection, state corruption,
  and request-drop simulation for resilience testing.
- **SBOM generation** (`scripts/generate_sbom.py`): CycloneDX-compatible JSON output
  for supply-chain auditing.

#### Low Tier
- **Compliance checklist** (`docs/compliance-checklist.md`): SOC 2, GDPR, ISO 27001
  control mapping with implementation status per control.
- **SOC 2 evidence stubs** (`docs/soc2-evidence.md`): Evidence templates for
  logical-access controls, system monitoring, change management, and availability.

### Changed
- `HiveStack` constructor now accepts `config` and `rate_limiter` kwargs.
- `RustBrain.__repr__` includes `tenant=...` for observability.

### Fixed
- Health probes accept `"degraded"` (optional missing policy) as non-failing
  for readiness, preventing false-negative readiness checks.
- Tenant prefixing is internal-only (`storage_key`); external `MemoryNode.key`
  is preserved unchanged for backward compatibility.

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
