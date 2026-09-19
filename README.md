# Hive

**Keep the reasoning on the LLM. Move the routine work to the CPU.**

Hive is an orchestration layer for AI agents: route mechanical tool calls locally, trim repetitive context, and recall prior fixes. Add it to your agent loop without replacing your model.

[![Version](https://img.shields.io/github/v/release/DJLougen/hive?label=release)](https://github.com/DJLougen/hive/releases/latest)
[![Python](https://img.shields.io/badge/python-3.10+-green)](https://python.org)
[![Tests](https://github.com/DJLougen/hive/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/DJLougen/hive/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-yellow)](https://opensource.org/licenses/MIT)

[Quickstart](#run-it) · [Benchmark results](benchmarks/README.md) · [Integrate your agent](docs/HARNESS_SETUP.md)

---

## Why it matters

Your agent shouldn't need a paid reasoning call just to re-run tests after a patch. Hive can handle observable workflow transitions on the CPU and escalate decisions that need the model. Context compression and causal memory help reduce the material your agent sends and the work it repeats.

## The evidence: fewer paid decisions

In the published hard-tier benchmark, Hive used fewer LLM calls and less total API spend than an agent that asked the LLM to choose every action. Real tool execution, six tasks, hidden grading tests, and repeated runs with DeepSeek-V4.1-Flash:

| | baseline (LLM-everything) | context (escalate-only) | **hive (rule-routed)** |
|---|---|---|---|
| **Resolve rate** | 77/90 (86%) | 74/90 (82%) | **74/90 (82%)** |
| **Mean LLM calls** | 7.31 | 7.20 | **3.04** |
| **Total cost** | $0.368 | $0.394 | **$0.214** |
| **McNemar vs baseline** | — | not_separable (p=1.0) | **not_separable (p=1.0)** |

**The opportunity is lower orchestration cost—not a claim of higher intelligence.** This table measures the rule-based routing path; [the trained CPU router has a separate evaluation](benchmarks/README.md). Resolve counts were lower than baseline, and “not separable” does not prove equal quality. These small, project-authored benchmarks support a pilot, not a guarantee for your workload.

[Explore the results and reproduce the runs](benchmarks/README.md) · [Audit the provenance and retractions](docs/benchmarks/PROVENANCE.md)

## What it does

Three capabilities you can wire into your existing agent:

1. **Spend model calls on reasoning.** Route supported mechanical steps from observable state; escalate when the policy cannot make an accepted, executable decision.
2. **Keep useful context, trim repetition.** Compress tool output and logs before they enter the next model call.
3. **Reuse what worked.** Recall prior fixes through causal memory instead of starting every repeat from scratch.

```text
   agent request → HiveStack → { route, compress, remember } → LLM (only when needed)
```

**Best fit:** developers who own an agent's tool loop and want to measure its routing and context costs. Start with a controlled pilot against your existing harness, keep your task-quality checks, and compare total cost per successful task. Hosted semantic routing is optional and is not the source of the headline result.

## Run it

> **Not yet on PyPI** — install from source. `pip install hive-agent-memory` is
> planned but the name does not resolve on PyPI yet.

```bash
git clone https://github.com/DJLougen/hive && cd hive
pip install -e .                           # base: rule_fast + rust_brain
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

v0.7.0 (Beta, unreleased — last release v0.6.1). Routing-accuracy numbers are *in-distribution* — see the OOD caveat in [`docs/architecture.md`](docs/architecture.md). **PFN / busyBee-cpu training-mode integration** is in progress ([busyBee-cpu](https://github.com/DJLougen/busyBee-cpu)).

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
