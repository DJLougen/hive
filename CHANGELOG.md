# Changelog

All notable changes to Hive are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-06-01

### Added
- `hive.rule_fast`: in-repo rule-based context compressor. ~36 k msg/s on x86_64,
  drop-in compatible with `honeycomb.HoneyComb`'s public surface.
- `hive.hardware`: NVML-based power and memory sampler; trapezoidal energy
  integration; graceful degradation when pynvml is absent.
- `hive.llm`: unified LLM client (vLLM / llama.cpp / echo) with
  `/v1/models` endpoint probing.
- `hive_benchmark_micro.py`: per-component micro-benchmarks with mean ± stdev.
- Statistical envelope on the macro benchmark (`--runs N`).
- `tests/` suite: 37 tests covering rust_brain, stack, hardware, llm, and
  the benchmark CLI.
- `Dockerfile.aarch64` for Jetson Thor / Grace.
- GitHub Actions CI matrix: x86 CPU × 3 Python versions, self-hosted GPU,
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
