# Hive

**Orchestration layer for AI agents** — CPU-side action routing, context compression, and causal graph memory that keep mechanical work and context bloat off the LLM.

[![Version](https://img.shields.io/github/v/release/DJLougen/hive?label=release)](https://github.com/DJLougen/hive/releases/latest)
[![Python](https://img.shields.io/badge/python-3.10+-green)](https://python.org)
[![Tests](https://github.com/DJLougen/hive/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/DJLougen/hive/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-yellow)](https://opensource.org/licenses/MIT)

---

## Why it matters

An agent loop spends most of its LLM calls on *mechanical* work — list the files, run the tests, read the file the traceback named, re-run the tests. Each of those is a paid call with the whole transcript attached. Hive answers those on the CPU and only escalates the decisions that actually need reasoning. The model sees less, pays less, and resolves the same tasks.

## The headline — hard tier, n=15, held-out oracle

On a 6-task benchmark built to *separate* the arms (real repos, hidden pytest oracle injected only at grading, DeepSeek-V4.1-Flash, n=15, spec review equalized):

| | baseline (LLM-everything) | context (escalate-only) | **hive (CPU-routed)** |
|---|---|---|---|
| **Resolve rate** | 77/90 (86%) | 74/90 (82%) | **74/90 (82%)** |
| **Mean LLM calls** | 7.31 | 7.20 | **3.04** |
| **Total cost** | $0.368 | $0.394 | **$0.214** |
| **McNemar vs baseline** | — | not_separable (p=1.0) | **not_separable (p=1.0)** |

Hive matches the LLM-everything baseline task-for-task at **58% fewer LLM calls** and **~45% lower cost** — the routing is free capability, not a capability tax. On the easier 10-task suite it's starker: identical 30/30 resolve at **−83% LLM calls** and **−82% prompt tokens**.

Full per-task tables, the three retracted runs that got us here, and the honest caveats: [`benchmarks/README.md`](benchmarks/README.md) · [`docs/benchmarks/PROVENANCE.md`](docs/benchmarks/PROVENANCE.md).

## What it does

Three jobs, all on the CPU, before the LLM is involved:

1. **Routes mechanical decisions** — `read_file`, `run_tests`, `apply_patch` go to a CPU policy. No LLM call. Out-of-distribution states escalate instead of guessing.
2. **Compresses context** — a content-aware classifier drops or distills the wax (stale logs, unchanged files) so the LLM sees only the honey.
3. **Remembers causally** — a timestamped graph records cause → effect → supersession, so the agent stops re-deriving what it already learned.

```text
   agent request → HiveStack → { route, compress, remember } → LLM (only when needed)
```

## Run it

```bash
pip install hive-agent-memory              # base: rule_fast + rust_brain
pip install "hive-agent-memory[full]"      # + trained CPU router + ML compressor
```

```python
from hive import HiveStack

stack = HiveStack()
result = stack.step(
    {"goal": "Fix auth bug", "step": 1},
    [("user", "login is failing"), ("assistant", "checking logs...")],
)
result["decision"]    # RouteDecision — CPU-routed or escalated
result["compressed"]  # CompressedTurn — what the LLM actually sees
```

Reproduce the benchmark:

```bash
python scripts/hive_bench.py --backend openai \
    --endpoint <openai-compatible-url> --api-key-env <KEY> --model <model> \
    --suite benchmarks/tasks/suite.hard.json --arm all --repeat 15
```

## Where to go next

| I want to… | Read |
|---|---|
| Understand the benchmark numbers | [`benchmarks/README.md`](benchmarks/README.md) |
| See the full API (`route`, `compress`, `remember`, `recall`, `step`) | [`docs/USAGE.md`](docs/USAGE.md) |
| Wire it into an agent harness | [`docs/HARNESS_SETUP.md`](docs/HARNESS_SETUP.md) |
| Use it from Cursor / Claude Desktop / Codex | [`docs/MCP_SETUP.md`](docs/MCP_SETUP.md) |
| Understand the architecture & modules | [`docs/architecture.md`](docs/architecture.md) |
| Deploy it (Docker, K8s, enterprise) | [`docs/`](docs/) |

## Status

v0.7.0 (Beta). Routing-accuracy numbers are *in-distribution* — see the OOD caveat in [`docs/architecture.md`](docs/architecture.md). **PFN / busyBee-cpu training-mode integration** is in progress ([busyBee-cpu](https://github.com/DJLougen/busyBee-cpu)).

## Roadmap

- [x] Core orchestration (routing, compression, causal memory)
- [x] Enterprise modules (auth, encryption, audit, rate limiting, multi-tenancy)
- [x] Native Rust backend (hive-cpp) with multi-platform wheels
- [x] Real-workload held-out A/B evaluation (three tiers, honest provenance)
- [x] MCP + FastAPI agent extras (`[agents]`, `[server]`, `[mcp]`)
- [ ] PFN-based busyBee training mode (inference + campaign retrain from `FeedbackBuffer`)
- [ ] Durable distributed memory backend (gossip replication in place; durability pending)
- [ ] Kubernetes operator for autoscaling
- [ ] Broader out-of-distribution routing coverage

## License & citation

MIT — see [LICENSE](LICENSE). Cite via [`.github/citation.cff`](.github/citation.cff):

```bibtex
@software{hive2026,
  title  = {Hive: orchestration layer for AI agents},
  author = {Lougen, Daniel J.},
  year   = {2026},
  url    = {https://github.com/DJLougen/hive}
}
```

Issues: <https://github.com/DJLougen/hive/issues> · Discussions: <https://github.com/DJLougen/hive/discussions>
