# Hive Improvement Plan

**Last updated:** 2026-08-25

Handoff plan for `DJLougen/hive`. Most Phase 1–3 items are **done** as of v0.6.1; this file tracks completion status.

---

## Phase 1: Real-workload evaluation — ✅ DONE

| Task | Status | Evidence |
|------|--------|----------|
| 1.1 SWE-bench-lite A/B eval | ✅ | `scripts/hive_swebench_eval.py`, `docs/benchmarks/swebench-lite/` |
| 1.2 Compression sensitivity | ✅ | `scripts/hive_compression_sweep.py`, README table |
| Long-context compression eval | ✅ | `scripts/hive_long_context_eval.py` |

**Follow-up:** Re-run on 50 instances with a 7B+ model when GPU CI is available.

---

## Phase 2: README claim corrections — ✅ DONE (v0.6.0)

Routing framed as in-distribution; energy/ROI headlines removed; device matrix trimmed.

---

## Phase 3: Packaging and distribution — ✅ MOSTLY DONE

| Task | Status | Notes |
|------|--------|-------|
| 3.1 Publish to PyPI | ✅ | `hive-agent-memory` on PyPI |
| 3.2 Tag releases | ✅ | v0.6.1, release workflow |
| 3.3 Rename rust_brain | ⏸️ | Documented in module docstring; rename deferred |

---

## Phase 4: API hardening — ✅ DONE

| Task | Status | Evidence |
|------|--------|----------|
| 4.1 Logical clocks (HLC) | ✅ | `HybridLogicalClock` in `hive/rust_brain/` |
| 4.2 Multi-writer tests | ✅ | `tests/test_rust_brain_concurrency.py` |
| HLC snapshot/gossip preservation | ✅ | `tests/test_hlc_snapshot_gossip.py` |

---

## Phase 5: Polish — ✅ DONE

- CITATION.cff validation in CI
- Eval-result issue template
- MCP + agents optional extras
- `uv.lock`, pre-commit, Dependabot

---

## Modernization (2026-08-25)

| Tier | Items | Status |
|------|-------|--------|
| 1 | Tooling lockfile, ruff, Python 3.13 CI, HLC fix | ✅ |
| 2 | MCP/API extras, long-context eval, CI smoke | ✅ |
| 3 | `HIVE_BACKEND`, LinUCB, httpx async LLM | ✅ |
| 4 | Docs refresh, SBOM/pip-audit, Docker bump, GPU nightly CI | ✅ |

---

## Definition of done for v0.7

- [ ] README leads with 50-instance SWE-bench on a modern 7B+ model
- [ ] hive-cpp wheels on PyPI; `HIVE_BACKEND=native` is default when installed
- [ ] Compression eval shows >2× ratio on long-context workloads
