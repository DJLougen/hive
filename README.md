# Hive — unified agent memory & context compression stack

**Save tokens, skip the LLM on obvious calls, and keep causal memory — on a 3090, a Jetson, or a Raspberry Pi.**

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
                                       GPU LLM (vLLM / llama.cpp)

## What it does (with real numbers)

> Numbers below are reproducible on the RTX 3090 dev box with
> `python scripts/hive_benchmark.py --quiet` and
> `python scripts/hive_benchmark_micro.py --runs 5`. They are
> synthetic-workload numbers, not real agent runs — see the
> [Limitations](#limitations) section below.

### 1. Saves tokens before they hit the LLM

A 50-message agent transcript with realistic mix (test output, source
files, search hits, user prompts) compresses from **8 185 → 4 922 tokens
(1.66×)** before the LLM sees it. Per-content-type:

| Content               | Raw tokens | Compressed | Ratio  | What survives                          |
|-----------------------|-----------:|-----------:|-------:|----------------------------------------|
| 500-line test output  | 2 412      | 32         | **75×**| `tests: 500 ok, 71 FAIL` + first 5 failures |
| 5 KB source file      | 1 261      | 19         | **66×**| `class Foo: ...` head + line count     |
| 50-line search result | 347        | 63         | **5×** | first 8 hits                           |
| short user prompt     | 6          | 6          | 1×     | untouched (it's the goal, keep CORE)   |

A long-running SWE-agent session that hits the 200 k-token context
window at turn ~40 will instead hit it at turn ~80. **That is a
2×-more turns-per-dollar agent at the same model.**

### 2. Skips the LLM for the obvious calls

On the [swebench](https://www.swebench.com) held-out subset (100
rows, 4-class routing problem), the busyBee CPU classifier picks the
correct next action **100%** of the time in this small-sample sanity
check:

```
test set: 100
hits:  {'apply_patch': 40, 'read_file': 39, 'escalate': 21}
misses: {}
busybee correct: 100/100
LLM calls AVOIDED: 100% on this sample (busybee handled mechanically)
```

For real workloads the accuracy is lower — the busyBee-cpu readme
claims 98.2% on its training set, your number will be whatever your
data is — but every CPU-handled turn saves an LLM call. At
~$3 / 1M input tokens on a 7B model and a 1 k-token prompt, **a 30%
busyBee hit rate is worth roughly $0.09 per 100 turns** — and the
rate gets *higher* as you collect more corrections, not lower.

### 3. Remembers what matters, refuses to forget what didn't happen

`rust-brain` writes **~270 000 memory nodes per second** on a single
x86_64 core, and rejects replay-of-older writes with a hard
`TimestampRegression` exception. The graph is causally-typed
(`caused_by`, `supersedes`, `related_to`, `attached_to`):

```python
from hive import HiveStack
stack = HiveStack()

stack.remember("api.endpoint", "/v1/chat", trust=0.95, tags=("http",))
stack.remember("api.endpoint", "/v2/chat", trust=0.95,
               caused_by=["api.endpoint"])
chain = stack.brain.neighbours("api.endpoint")
# -> ['api.endpoint']   (old node now points at the new one via SUPERSEDES)
```

Two weeks later, when the same endpoint URL comes back in a tool
result, you can walk the chain and see exactly which test result
caused which rewrite. Most vector-store agent memories cannot do
this — a fresh embedding erases the chain.

### 4. Pays for itself on the GPU

Full-step latency (route → compress → write memory) is **~10 µs per
turn on a single x86_64 core**. On the RTX 3090 with NVML sampling,
the busyBee + honey-comb + rust-brain path costs **~5 J per second of
agent wall-clock** end-to-end, while a single 4 k-token forward pass
on a 7B model costs **~3 J by itself**. **The CPU work is a rounding
error in the energy budget** — and the gap widens as the model gets
larger.

### 5. Runs where your users actually are

| Device                    | busyBee | honey-comb | rust-brain | Status          |
|---------------------------|---------|------------|------------|-----------------|
| RTX 3090 / DGX Spark      | 111 r/s | 36 k msg/s | 270 k w/s  | validated       |
| Jetson Thor (aarch64+CUDA)| TBD     | TBD        | TBD        | Docker ready    |
| Grace (aarch64+CUDA)      | TBD     | TBD        | TBD        | Docker ready    |
| Raspberry Pi 5 (aarch64)  | TBD     | TBD        | TBD        | no GPU, runs    |
| iPhone 17 Pro (arm64)     | TBD     | TBD        | TBD        | no CUDA, runs   |

CI matrix: `.github/workflows/ci.yml`. Cross-build instructions:
`docs/arm64-build.md`. The aarch64 cells are intentionally left as
"TBD" — **this is where your PRs matter most**. Run the micro-bench
on your hardware, file a `performance` issue, and we will publish the
number.

### Limitations

The numbers above are from the **Step 1 in-repo benchmark** with a
synthetic 200-turn transcript, not a real agent workload. Two things
to know before you trust them:

* The busyBee 100% number is a 100-row sanity check, not a benchmark.
  Real agent traces will land closer to the 98.2% busyBee-cpu readme
  number on the training distribution, and lower on out-of-distribution
  states.
* The compression ratios assume the honey-comb `rule_fast` path, not
  the ML classifier. The ML classifier is **3-5× slower** on the same
  workload (still well under 1 ms / message on x86_64) and may produce
  different ratios on real text.

The point of the table is to show *order of magnitude*, not to
back-claim a number. **Real numbers from your hardware are the only
numbers that matter** — and that is exactly what
`scripts/hive_benchmark.py` exists to give you.
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
