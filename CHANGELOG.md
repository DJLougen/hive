# Changelog

All notable changes to Hive are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- `hive_bench.py` held-out grading (`grade_patch`): the agent's writes are replayed into a pristine copy of the task repo and graded by a hidden pytest suite injected only there — held-out tests never appear in the agent's workdir. `Task`/`load_tasks` gain `oracle_dir`/`oracle_cmd`/`solution_patch`; `StepLog.ok` records write success; `--verify-tasks` is a no-cost gate that fails closed (smoke green before, hidden suite red before, green after the reference patch, no `tests/` shipped in the repo) and is wired into `verify.sh` + CI.
- `hive_bench.py` arms and statistics: `--arm context` (escalate-only control) and `--arm all`, `--memory {fresh,shared}`, `--price-in/--price-out`; Wilson 95% CI, unbiased pass^k, exact McNemar over per-task majority outcomes, and a `verdict` that can say `at_ceiling` / `not_separable` / `separated`. Resumed episodes (`--skip-existing` + `--scratch`) mark `usage_recorded=False`; `summarize` publishes null usage fields for any arm containing them — never a zero that reads as "free".
- Six held-out benchmark tasks (`retry-budget-shared`, `cache-key-collision`, `pagination-cursor-drift`, `csv-encoding-chunking`, `interval-merge-tiebreak`, `config-precedence-layers`), each ≥4 modules with a second spec-implied requirement so partial fixes still fail; `solutions_public_since` recorded per task.
### Added
- `scripts/hive_bench.py` + `benchmarks/tasks/`: real tool-execution benchmark — real repos with real failing pytest suites, real LLM over OpenAI-compatible endpoints (incl. function calling + bearer auth), and a real `pytest` resolve gate. First run: 10/10 resolved on both arms, −83% LLM calls, −80% prompt tokens for the Hive arm (`docs/benchmarks/hive-bench-flash.json`). **Re-run 2026-09-17, 3 repeats × 10 tasks = 30 episodes per arm** (`docs/benchmarks/hive-bench-flash-r3.json`): 30/30 resolved on both arms, −83.3% LLM calls, −82.1% prompt tokens, −48.5% completion tokens, +16.7% turns, −68.7% wall clock, 24/30 memory hits. Per-pass LLM calls: baseline 5.9/6.1/6.0 (stderr 0.06), Hive 1.0/1.0/1.0 (stderr 0.00) — the single-pass run is superseded.
- `hive/cpu_policy.py` (`CPURouterPolicy`): trainable CPU routing policy — RandomForest over observable-state features, trained by imitating logged `state -> action` trajectories (`scripts/hive_bench.py --log`, `scripts/train_cpu_policy.py`), with per-tool arg resolvers, a confidence-floor escalation, a write-without-recalled-fix refusal, and an identical-route loop guard. Drops into `HiveStack(busybee_policy=...)`. Configurable tool vocabulary (`tools=`); `TRACE_TOOLS` covers the canonical set for real-agent traces. Measured on hive-bench: 10/10 resolved with 1.8 mean LLM calls on first sight, and **0 LLM calls** on `--repeat` pass 2 via memory replay (`docs/benchmarks/hive-bench-cpu-policy.json`).
- `benchmarks/traces/` + `scripts/extract_traces.py` + `scripts/trace_bench.py`: 100 deidentified tool-call traces from real omp/prime-agent session logs (5 stratified workflow families; only canonical tool names, ok/error flags, step indices, histograms, and arg key-classes are stored — no text, paths, or values). `trace_bench.py` reports coverage/fidelity/executable bounds with Wilson CIs, supports `--pool` full-corpus training with eval ids excluded, an `--algorithms` bake-off, and a `--curve` learning curve.
- `benchmarks/traces-all/` (1,695 deidentified traces) and `benchmarks/traces-hf/` (300 Hermes traces via `scripts/fetch_hf_traces.py` from `ThreeSixNine/hermes-agent-reasoning-traces`) as training corpora.
- Algorithm bake-off on the full pool (`docs/benchmarks/trace-bakeoff.json`, 10,960 held-out steps): mlp 48.0% / markov2 47.5% / hgb 46.8% raw next-tool accuracy vs 43.1% repeat-last and 37.9% majority. Learning curve shows sequence models saturate almost immediately and feature models gain ~17 points with data — tool choice is largely Markovian, and tool-output content remains the missing signal.
- `CPURouterPolicy` pluggable algorithms (`algorithm=`): rf, rf-deep, extratrees, hgb, logreg, mlp (early stopping), markov1/markov2 n-gram transition models.
- `hive_bench.py` flags: `--log` (trajectory logging), `--repeat` (memory-replay passes), `--policy rule|trained`, `--policy-path`.
- `hive_bench.py` artifacts carry provenance (policy, policy_path, policy_class, temperature, repeat, git sha + dirty flag, timestamp) and a dispersion block: per-pass means plus mean/stderr/range across passes, with `stderr: null` when only one pass ran, so a single run cannot be read as a point estimate with a known spread.
- `_OpenAICompatBackend` bearer-token auth (`api_key=`), `tools`/`tool_choice` function-calling support, `openai` backend name in `make_backend`, and a `User-Agent` header (fixes 403s on endpoints behind bot filtering).
- `uv.lock`, `.pre-commit-config.yaml`, and Dependabot for reproducible dev tooling.
- Optional extras: `server`, `mcp`, `agents`, `http` (FastAPI, MCP, httpx).
- `hive/backend.py` with `HIVE_BACKEND=python|native|auto` wiring in `HiveStack`.
- LinUCB contextual bandit policy in `hive/policy_updater.py`.
- Async LLM client via `httpx` (`_OpenAICompatBackend.achat`).
- Long-context compression eval: `scripts/hive_long_context_eval.py`.
- HLC preservation tests for snapshot restore and gossip replay.
- Gossip publish wiring (`HiveStack(gossip=…)` / `AsyncHiveStack(gossip=…)`) and a bounded in-process audit trail (`HiveConfig(audit_enabled=True)`, `stack.audit_events()`) for SIEM export.
- `rust_brain.history()`: bounded per-key history of superseded versions, persisted in snapshots behind `history_sha256`.
- Optional bearer-token auth for gossip and the REST API (`HIVE_API_TOKEN`); Ed25519 model signature verification with `sign_model()`.

### Removed
- `scripts/hive_swebench_eval.py` simulated the agent loop and drew resolve outcomes from an RNG — its reported numbers (85% vs 0%) were not real. Replaced with a deprecation shim forwarding to `hive_bench.py`; fabricated raw runs under `docs/benchmarks/swebench-lite/` deleted.

### Changed
- `RuleBasedRoutingPolicy` routed on `action_hint` — a field only the removed fake eval ever set. It now drives a real workflow state machine (enumerate → reproduce → read traceback target → verify → finish) on observable state, keeping the keyword fallback for generic states.
- CI: Python 3.13 matrix, pip-audit, SBOM job, MCP smoke, long-context smoke, nightly GPU/Jetson.
- `restore_from_file` and gossip `receive` preserve HLC timestamps.
- Docker aarch64 base image bumped to L4T r36.4.0; numpy 2.x allowed.
- Enterprise roadmap and improvement plan refreshed to reflect shipped features.
- `validate=True` normalizes state before `route()` and validates writes via `validate_memory()`; `record_outcome()` matches a bounded window of recent `route()` calls so out-of-order feedback lands on the right state.

### Fixed
- **Label leakage in the trace benchmark**: `featurize` consumed `n_args`/`has_path`/`has_pattern`/`has_cmd` — features of the *current* call's arguments, which only exist after the tool is chosen. They produced a spurious ~72% argmax; removing them gives the honest ~48% ceiling. Fields remain in stored traces as metadata but are excluded from the feature vector.
- `_MarkovModel` backoff never found shorter contexts (counts only stored full-order keys); now keeps per-order tables.
- `trace_bench` only wrote its artifact at the end — a kill lost hours of fits. Now checkpoints after every model, predictions are batched (~100x faster eval), and `--curve-only` resumes a finished bake-off for the learning curve.
- `rule_fast` compressor destroyed small file reads (`[file] N lines` stub for a 649-byte source file) and stripped test failures down to a count. Small files now stay `CORE`; large files compact to a code skeleton (imports/defs/classes); test output keeps pass/fail counts plus failing asserts and file:line refs.
- `rust_brain`: snapshot restore now restores `hlc` fields and updates high-water mark.
- `gossip.receive`: replays remote `hlc`/`ts_ns` instead of generating new timestamps.
- `supersede()` keeps `SUPERSEDES` edges on the live node so chains stay walkable; `policy_updater` records `actual_action`; `auth.from_jwks` uses stdlib `urllib` (no `requests` dependency); assorted review fixes (token-bucket locking, telemetry OTel spans, streaming decision source, semantic index staleness).

### Security
- `CPURouterPolicy.load()` now requires a valid `.joblib.sig` sidecar (Ed25519 + SHA-256 via `hive/model_registry.py`). An unsigned model is refused with an actionable message unless `HIVE_ALLOW_UNSIGNED_MODEL=1` is set, because `joblib.load` executes arbitrary pickle bytecode. A trust store (`HIVE_MODEL_TRUST_STORE`) pins the expected signer; without one the first load is trust-on-first-use (integrity, not authenticity).

### Changed
- **`HIVE_BACKEND=auto` (the default) no longer selects the native crate.** It resolves to the Python reference implementation unconditionally; `native` must be requested explicitly. Reason: the crate's compressor keeps only `ceil(n/2)` whitespace tokens and rejoins them, so a backend that flipped because a wheel happened to be importable silently changed the context a model sees. `resolve_backend()` also warns on an unrecognised `HIVE_BACKEND` value instead of ignoring it.
- **The pentest disposition moved out of the checks.** `Finding.accepted` is gone; a passing critical/high finding is blocking unless `scripts/pentest/accepted.json` lists it with an owner, a justification and an unexpired date. A check can no longer mark its own finding non-blocking, and CI fails on an expired or incomplete entry.
- `hive-mcp` builds its stack with `RuleBasedRoutingPolicy` by default and gained `--policy {rule,path}` / `--policy-path`, so the documented entry point can actually route (it previously returned `escalate`/`source=fallback` for every call). The `hive_route` result reports which policy answered.
- Zero-config posture is now loud once per process (a warning naming the disabled controls) and `HiveStack(config=…)` validates the config on construction instead of accepting `rate_limit=-1` silently. `HiveStack.stats()["controls"]` reports the active posture. Defaults were **not** flipped.
- Deploy manifests use real `HiveConfig` field names (`HIVE_MAX_MEMORY_NODES`), run the API server they probe, and require `HIVE_API_TOKEN` (Helm refuses to render without it; the k8s manifest ships a placeholder to replace).

### Fixed
- `route()` paired a decision with whatever state another thread wrote last (`self._last_state`); pending decisions now carry the caller's own state snapshot, so feedback lands on the state that produced it.
- `update_policy()` cleared the feedback buffer before the update could fail, destroying up to the whole buffer per transient failure; outcomes are now discarded only after a successful update.
- `FeedbackBuffer` drops the oldest outcome at capacity with a counter and a log (was silent), and its lock is actually used.
- `default_ttl_s` was inert: expiry is now enforced on `recall()`/`get()`/`in`, not only by an explicit `expire()`/`gc_expired()` call.
- `RustBrain.snapshot()` dropped the empty-string key (truthiness filter); membership in `_nodes` is now the test.
- `config.otel_endpoint` was read and discarded; it is forwarded to `Telemetry.enable_otel_traces(endpoint=…)`, which raises (rather than warning) when a configured endpoint has no tracing dependencies.
- `is_winner()` could promote a variant from variant-only data; it now requires both arms to reach the minimum sample count.
- `trace_bench` Wilson intervals treated clustered steps as independent draws; cluster-aware intervals are reported alongside (naive width 0.062 vs cluster 0.438 on the correlated fixture).
- `hive_long_context_eval.py`: the rows are input *lengths* over one compressor (the old "conservative/aggressive" labels read as two configurations), the summary states each input length, and `--output` still writes the artifact.
- hive-cpp: `Router::decide` no longer `expect()`s on a malformed model (a caller-supplied JSON could abort CPython under `panic = "abort"`); malformed nodes escalate with a reason. Unused dependencies (`rayon`, `dashmap`, `parking_lot`, `simd-json`, `xxhash-rust`) removed; the bench-only `rand` moved to dev-dependencies.
- Doc claims corrected against their artifacts: `docs/energy.md` (3 prompts / 148 tokens, not 445/396 and 10×3), `docs/soc2-evidence.md` + `docs/compliance-checklist.md` (encryption-in-transit and audit-log integrity marked *not evidenced*; the non-existent `hive/audit.py` reference removed), `docs/architecture.md` performance cells. `hive-cpp/CHANGELOG.md` no longer publishes unmeasured speedups.
- Lint: `scripts/` is now inside the ruff scope, with the four pre-existing errors fixed.

### Changed
- Published A/B numbers moved to the 3-repeat run (`docs/benchmarks/hive-bench-flash-r3.json`): 30 episodes per arm with recorded provenance and per-pass dispersion, superseding the single-pass `hive-bench-flash.json` (kept in the tree). The claim gate now checks the published stderr against the artifact's per-pass means (23 checks).

### Docs
- `CONTRIBUTING.md`: the review checklist now names the verification gate, the no-silent-degradation rule and "artifact or it doesn't ship".
- `scripts/verify.sh`: one command for everything CI enforces (ruff, mypy, suite + coverage floor, claim gate, pentest gate, `cargo test`, native adapter tests).
- `scripts/check_claims.py` now also checks the numbers restated outside the README (`benchmarks/README.md`, `docs/WHATS_NEW.md` — every occurrence) and recomputes the hive-bench cpu-policy per-pass means from the raw results list.
### Docs
- README: August 2026 "What's new" section with outcome table (HLC fix, MCP, long-context eval, `HIVE_BACKEND`, LinUCB).
- New `docs/WHATS_NEW.md` with tier summary and Twitter-ready copy.

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
