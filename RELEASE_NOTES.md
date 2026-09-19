# Hive Release Notes

This document contains release notes for tagged versions of Hive.

## v0.7.0 (unreleased)

### Highlights
**Hive now matches an LLM-everything agent on a held-out benchmark built to separate the arms — at 58% fewer LLM calls and ~45% lower cost.** This release is a benchmark-integrity pass: the number that was always true (CPU routing is free capability) is now *provably* true, on tasks hard enough to discriminate, with the grading exploits closed and the contamination vectors flagged.

### The headline — hard tier, n=15, held-out oracle

| | baseline (LLM-everything) | context (escalate-only) | **hive (CPU-routed)** |
|---|---|---|---|
| **Resolve rate** | 77/90 (86%) | 74/90 (82%) | **74/90 (82%)** |
| **Mean LLM calls** | 7.31 | 7.20 | **3.04** |
| **Total cost** | $0.368 | $0.394 | **$0.214** |
| **McNemar vs baseline** | — | not_separable (p=1.0) | **not_separable (p=1.0)** |

Six tasks authored to discriminate (`benchmarks/tasks/suite.hard.json`), every oracle rule disclosed in the problem statement, spec review equalized across all arms, hidden tests injected only at grading. Artifact: [`docs/benchmarks/hive-bench-hard.json`](docs/benchmarks/hive-bench-hard.json) (commit `79a24c7`, clean tree).

### What made it trustworthy (the integrity pass)

The routing result was always there; this release makes it *mean* something:

- **Closed the grading exploit** — `write_file` and `grade_patch` now reject writes to `conftest.py`, `pytest.ini`, `pyproject.toml`, `setup.cfg`, `tox.ini`, and `sitecustomize.py`/`usercustomize.py`. Previously an agent could deselect the hidden oracle tests and get `resolved` without fixing anything.
- **Deconfounded the routing claim** — every arm now gets one identical spec-review turn before `finish` is accepted (`_SPEC_REVIEW_NOTE`). Previously only hive's policy escalated on green, so the routing delta was confounded by a prompt advantage.
- **Fixed the trap tasks** — three tasks had oracle tests asserting behavior the problem statement never disclosed (lru-ttl sweep rule, snapshot negative-coverage, kway ordering); one had a vacuous smoke test green on the buggy repo. All disclosed or dropped — the suite now measures capability, not mind-reading.
- **Fixed the prompt** — `smoke_note` was telling agents the visible suite "is NOT the criterion" (the opposite of the gate). It now tells the truth: smoke is RED until fixed, `run_tests` is the reproduction signal.
- **Closed the contamination vector** — `oracle/solution.patch` files are untracked + gitignored (a memorized gold patch flips `resolved` directly); `oracle_public_in_git` is recorded in provenance so the exposure is honest.

### Added
- `benchmarks/tasks/suite.hard.json` — the 6-task discriminating tier manifest.
- `docs/benchmarks/PROVENANCE.json` + `PROVENANCE.md` — researchMax provenance record: the kept run, three retracted runs (each with the tell that exposed it), five closed levers, and the do-not list.
- `--verify-tasks` self-check: smoke RED on pristine, smoke GREEN after patch, oracle GREEN after patch, oracle absent from repo, pytest-control blocked.
- `oracle_public_in_git` provenance field — counts tracked `oracle/` files so contamination exposure is recorded, not guessed.

### Changed
- `load_tasks` strict `--tasks` (fails loudly on unknown ids), deduped scratch workdirs, `tzinfo` guard on the `solutions_public_since` cutoff.
- `smoke_note` prompt now states the real gate contract.
- README: hard-tier headline table at the top; capability tier and A1 suite retained below with full provenance.

### Honest caveats
- `oracle/tests/` remain git-tracked (CI + `--verify-tasks` need them), so **absolute resolve rates are contaminated** for a model that has seen this repo — the routing/cost delta is the defensible claim.
- `lru-ttl-cache` is the only task still discriminating (hive leads it 4/15 vs 2/15, 1/15); the other five are near-ceiling for all arms.
- The hive arm runs the rule-based state machine (`RuleBasedRoutingPolicy`), not the trained `CPURouterPolicy` — read the 3.04-vs-7.2 call delta as a routing result, not a learned-policy result.

### Migration
No code changes required. The benchmark harness is additive; `hive_bench.py` flags are backward compatible.

## v0.6.1 (2026-06-29)

### Highlights
Security fix for rust_brain snapshot restore, PyPI *publishing pipeline*, and refreshed dependency pins.

### Security
- `restore_from_file` now verifies SHA-256 checksums embedded in snapshots; tampered files raise `ValueError` and leave existing state untouched.

### Added
- PyPI publishing workflow: a `v*` tag builds wheels/sdist and **attempts** upload. *(No version has actually been published — `hive-agent-memory` does not resolve on PyPI as of 2026-09; install from source.)*
- `[full]` extra installs `busybee-cpu` and `honey-comb` (from git until they are on PyPI).

### Changed
- Core and optional dependency minimum versions bumped (see `pyproject.toml`).
- FastAPI server version tracks `hive.__version__`.

### Migration
No code changes required. Update version pins to `hive-agent-memory>=0.6.1,<0.7.0`.

## v0.6.0 (2026-06-11)

### Highlights
Real-workload evaluation, API hardening, and a documentation/metadata pass that brings the
repository in line with the v0.6.0 release.

### Added
- SWE-bench-lite A/B evaluation harness and committed benchmark runs.
- Compression sensitivity sweep.
- Hybrid Logical Clock for causal ordering; additional rust_brain concurrency tests.
- Release workflow and evaluation-result issue template.

### Changed
- README rewritten for accuracy and to reflect the full module surface; routing claims
  reframed as in-distribution with an explicit OOD caveat.
- Packaging metadata aligned to 0.6.0.

### Fixed
- Restored the `[build-system]` table in `pyproject.toml` and the `0.6.0` version across the
  package, the FastAPI server, and the Helm chart.
- Balanced README code fences and corrected the compression label names.
- `CITATION.cff` is now valid CFF 1.2.0 and validated as such in CI.

### Migration
No code changes required. Update version pins to `hive-agent-memory>=0.6.0,<0.7.0`.

## v0.5.0 (2026-06-02)

### Highlights
Enterprise-grade infrastructure for production deployments.

### Key Features
- **Multi-tenancy**: Tenant isolation with automatic key prefixing
- **Schema validation**: Pydantic models for state and memory validation
- **Rate limiting**: Token-bucket rate limiting per tenant/operation
- **Health probes**: Liveness and readiness endpoints for Kubernetes
- **Data retention**: TTL-based memory expiration with GDPR compliance
- **Observability**: Prometheus metrics and OpenTelemetry traces

### Performance
- Rate limiter: <1µs overhead per operation
- Schema validation: ~50µs per validation
- Health check: <1ms response time

### Breaking Changes
None. All changes are backward compatible.

### Migration Guide
No migration needed. New features are opt-in via configuration.

---

## v0.4.0 (2026-06-02)

### Highlights
Production observability and monitoring infrastructure.

### Key Features
- **Telemetry system**: Comprehensive metrics collection and export
- **Prometheus integration**: Real-time metrics endpoint
- **OpenTelemetry**: Distributed tracing support
- **JSONL export**: Batch and append-mode event logging
- **CI improvements**: Fixed workflow issues, added linting

### Performance
- Telemetry overhead: <5µs per operation
- Prometheus endpoint: <10ms response time
- JSONL export: ~100k events/sec

### Breaking Changes
None.

### Migration Guide
Enable telemetry via `HiveStack(enable_telemetry=True)`.

---

## v0.3.0 (2026-06-02)

### Highlights
Native Rust backend for high-performance workloads.

### Key Features
- **hive-cpp**: Optional Rust implementation of core components
- **Router**: 269x faster than Python (0.001ms vs 0.269ms)
- **Compressor**: 6.3x faster than Python baseline
- **Memory**: Lock-free concurrent operations
- **PyO3 bindings**: Seamless Python integration

### Performance
| Component | Python | Rust | Speedup |
|-----------|--------|------|---------|
| Router | 0.269ms | 0.001ms | 269x |
| Compressor | 0.1ms | 0.016ms | 6.3x |
| Memory Store | 0.020ms | 0.020ms | 1.0x |

### Breaking Changes
None. Rust backend is optional.

### Migration Guide
Install Rust backend: `pip install hive-cpp`
Enable via environment: `HIVE_USE_RUST=1`

---

## Release Process

### Creating a New Release

1. **Update version numbers**:
   ```bash
   # Update pyproject.toml
   version = "X.Y.Z"
   
   # Update __init__.py files
   __version__ = "X.Y.Z"
   ```

2. **Update CHANGELOG.md**:
   - Move items from "Unreleased" to new version section
   - Add release date
   - Summarize key changes

3. **Commit changes**:
   ```bash
   git add -A
   git commit -m "Release vX.Y.Z"
   ```

4. **Create and push tag**:
   ```bash
   git tag -a vX.Y.Z -m "Release vX.Y.Z"
   git push origin main --tags
   ```

5. **CI automatically** (`.github/workflows/release.yml`):
   - Builds wheels for Linux / macOS / Windows and an sdist
   - Extracts title from `RELEASE_NOTES.md` and body from `CHANGELOG.md` via `scripts/extract_release_notes.py`
   - Creates a GitHub Release named `Hive vX.Y.Z — <highlights>`
   - Uploads wheel/sdist artifacts to the release
   - Publishes to PyPI when trusted publishing is configured (`docs/PYPI.md`)

### Release Notes Format

Each release should include:
- **Highlights**: 1-2 sentence summary
- **Key Features**: Bullet list of major additions
- **Performance**: Benchmarks if applicable
- **Breaking Changes**: API changes requiring migration
- **Migration Guide**: Steps to upgrade from previous version

### Version Numbering

Hive follows [Semantic Versioning](https://semver.org/):
- **MAJOR** (X.0.0): Breaking API changes
- **MINOR** (0.X.0): New features, backward compatible
- **PATCH** (0.0.X): Bug fixes, backward compatible
