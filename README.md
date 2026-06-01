# Hive — unified agent memory & context compression stack

> **One CPU-only fast path. One GPU-bound slow path. One timestamp-protected
> graph between them.**

<p align="center">
  <img src="https://img.shields.io/badge/version-0.2.0-22c55e?style=flat-square" alt="version"/>
  <img src="https://img.shields.io/badge/status-step%201%20shipped-22c55e?style=flat-square" alt="status"/>
  <img src="https://img.shields.io/badge/python-3.10%2B-3b82f6?style=flat-square" alt="python"/>
  <img src="https://img.shields.io/badge/tests-37%20passed-22c55e?style=flat-square" alt="tests"/>
  <img src="https://img.shields.io/badge/license-MIT-64748b?style=flat-square" alt="license"/>
  <img src="https://img.shields.io/badge/arm64%20%2F%20jetson%20thor-ready-8b5cf6?style=flat-square" alt="arm64"/>
  <img src="https://img.shields.io/badge/nvidia%20grace%20%2F%20spark%20%2F%203090-validated-22c55e?style=flat-square" alt="nvidia"/>
</p>

**The 2026 agent memory stack for NVIDIA + edge.**

Hive glues three independently-developed components into a single Python
API and a single benchmark surface, so an agent harness can keep the
bee theme and stop hand-rolling memory plumbing.

```
                  ┌──────────────────────────────────────────────┐
                  │                  Hive                        │
   user request ─►│  route ─► compress ─► llm ─► remember        │─► response
                  │  │           │              │                │
                  │  ▼           ▼              ▼                │
                  │ busyBee    honey-comb    rust-brain          │
                  │ (CPU)      (CPU+rules)   (CPU, graph)        │
                  └──────────────────────────────────────────────┘
                                            │
                                            ▼
                                       GPU LLM (vLLM / llama.cpp)
```

## Why now — 2026 positioning

Three forces are colliding in 2026 that make this stack timely:

1. **Agentic AI is shipping on NVIDIA.** Vera CPU + Grace + Jetson Thor
   are the default target for long-running agents, and the LLM is *one*
   cost in a larger loop. Memory, routing and compression are the
   others.
2. **The CPU is the bottleneck.** Once the LLM is on the GPU, every
   wall-clock second spent routing, compressing, or remembering is a
   second the agent is *not* using the GPU. Hive's hot path is CPU-only
   and tuned for cache-friendly access patterns.
3. **Edge is real.** Phones, Raspberry Pis, and Jetson boards need the
   same agent capability as a 3090 — without the same power budget.
   Hive runs on the same Python on every device, with no mandatory CUDA.

## Components

| Component                                                                 | Role                                                              | Where it lives     |
|---------------------------------------------------------------------------|-------------------------------------------------------------------|--------------------|
| **[`busyBee-cpu`](https://github.com/DJLougen/busyBee-cpu)**              | CPU-only action routing — answers "which of the 4 tools?"         | sibling package    |
| **[`honey-comb`](https://github.com/DJLougen/honey-comb)**                | Inline context compression — keep the honey, drop the wax         | sibling package    |
| **[`rust-brain`](hive/rust_brain/__init__.py)**                           | Timestamped graph memory with Hermes integration (Python shim)    | this repo          |
| **[`rule_fast`](hive/rule_fast/__init__.py)**                             | In-repo rule-based compressor (fallback if honey-comb is absent)  | this repo          |
| **[`hive.hardware`](hive/hardware.py)**                                   | NVML power + GPU memory sampler                                   | this repo          |
| **[`hive.llm`](hive/llm.py)**                                             | Unified LLM client (vLLM / llama.cpp / echo)                      | this repo          |

The Python shim of `rust-brain` is the *reference* implementation. The
Rust port (`hive-cpp`) is the planned Step 2 — see
[`docs/future-cpp.md`](docs/future-cpp.md).

## Status

| Step | Scope                                                                          | Status          |
|------|--------------------------------------------------------------------------------|-----------------|
| 1    | Python meta-package, validated on RTX 3090 / DGX Spark, ARM64-ready           | **shipped**     |
| 2    | Native `hive-cpp` port of busyBee + rust-brain                                | planned         |
| 3    | Native port of honey-comb + llama.cpp FFI                                     | planned         |
| 4    | NVIDIA hand-off: Vera / Thor reference implementation                          | planned         |

**Step 1 acceptance criteria** (all met on RTX 3090 / Spark + ARM64 build):

* [x] Single Python import (`from hive import HiveStack`) glues all three
      components.
* [x] `hive_benchmark.py` measures peak host + GPU memory, compression
      ratio, throughput, **and** real NVML joules.
* [x] Integration example wires Hive in front of a vLLM / llama.cpp server.
* [x] `docker/Dockerfile.aarch64` builds on Jetson Thor / Grace.
* [x] Component READMEs updated with Hive branding.
* [x] 37 tests pass in `pytest -q`.
* [x] Per-component micro-benchmark with statistical envelope.
* [x] CI matrix covers x86 CPU, x86+CUDA, aarch64.

## Quick start

```bash
# 1. Clone all three repos side-by-side. The hive meta-package depends
#    on busyBee-cpu and honey-comb as sibling packages.
git clone https://github.com/DJLougen/hive
git clone https://github.com/DJLougen/busyBee-cpu
git clone https://github.com/DJLougen/honey-comb
cd hive

# 2. Install.
python -m venv .venv && . .venv/bin/activate
pip install -e ../busyBee-cpu ../honey-comb .

# 3. Run the benchmark.
python scripts/hive_benchmark.py --quiet

# 3b. (Optional) per-component micro-benchmark with statistical envelope.
python scripts/hive_benchmark_micro.py --runs 5 --output runs/micro.json

# 4. Run the integration example (with a vLLM server already up).
python examples/hive_llama_integration.py --inference-backend vllm --inference-endpoint http://127.0.0.1:8000

# 5. Or, on a Jetson:
docker build -f docker/Dockerfile.aarch64 -t hive:aarch64 .

# 6. Run the test suite.
pytest -q
```

## Five-line tour

```python
from hive import HiveStack

stack = HiveStack()                # busyBee (optional) + honey-comb + rust-brain

decision = stack.route({            # busyBee-cpu: "what next?"
    "goal": "ship step 1",
    "state": {"current_step": 0, "last_tool": None},
    "available_tools": [{"name": "read_file"}, {"name": "escalate"}],
})
print(decision.tool, decision.confidence)

compressed = stack.compress(         # honey-comb: keep the honey
    role="tool",
    content="tests: 12 passed, 2 failed (test_session_invalidation) ...",
)
print(compressed.label, compressed.ratio)

stack.remember("endpoint", "/v1/chat", trust=0.9)  # rust-brain: timestamped
```

## Repository layout

```
hive/
├── README.md                  ← you are here
├── CHANGELOG.md               ← release notes (Keep a Changelog)
├── LICENSE                    ← MIT
├── CONTRIBUTING.md            ← how to send a PR
├── CODE_OF_CONDUCT.md         ← Contributor Covenant v2.1
├── SECURITY.md                ← how to report a vulnerability
├── pyproject.toml             ← meta-package definition
├── hive/
│   ├── __init__.py            ← lazy import for HiveStack
│   ├── stack.py               ← orchestrator (busyBee + honey-comb + brain)
│   ├── hardware.py            ← NVML power + memory sampler
│   ├── llm.py                 ← vLLM / llama.cpp / echo client
│   ├── rust_brain/            ← in-repo reference implementation
│   └── rule_fast/             ← in-repo rule-based compressor fallback
├── scripts/
│   ├── hive_benchmark.py      ← macro benchmark (the Step 1 deliverable)
│   ├── hive_benchmark_micro.py← per-component micro-benchmarks
│   └── smoke_rust_brain.py    ← minimal smoke test
├── examples/
│   └── hive_llama_integration.py   ← vLLM / llama.cpp integration
├── docker/
│   └── Dockerfile.aarch64     ← Jetson Thor / Grace image
├── tests/                     ← 37 pytest tests
└── docs/
    ├── architecture.md
    ├── arm64-build.md
    └── future-cpp.md
```

## Components — updated READMEs

* [`busyBee-cpu/README.md`](https://github.com/DJLougen/busyBee-cpu/blob/main/README.md)
  — CPU action routing; Hive's first stage.
* [`honey-comb/README.md`](https://github.com/DJLougen/honey-comb/blob/main/README.md)
  — Inline context compression; Hive's second stage.
* [`rust-brain` reference](HIVE_README_rust_brain.md) — timestamped graph
  memory; the in-repo Python shim is the reference oracle for the
  upcoming Rust port.

## Roadmap

See [`docs/future-cpp.md`](docs/future-cpp.md) for the full plan, and
[`docs/architecture.md`](docs/architecture.md) for the design. The
headline goal is to keep Hive on a single Rust binary that fits on a
Jetson Thor and still scales to a Grace rack.

## Contributing

We welcome bug reports, performance measurements, and small PRs. Please
read [`CONTRIBUTING.md`](CONTRIBUTING.md) and the
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) before opening an issue.

## License

MIT — see [LICENSE](LICENSE).
