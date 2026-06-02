# Hive

<p align="center">
  <a href="https://github.com/DJLougen/hive/actions"><img src="https://img.shields.io/badge/CI-13%20passed-brightgreen" alt="CI Status"></a>
  <a href="https://github.com/DJLougen/hive"><img src="https://img.shields.io/badge/version-0.3.0-blue" alt="Version"></a>
  <a href="https://github.com/DJLougen/hive/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="License"></a>
  <img src="https://img.shields.io/badge/python-3.10+-blue" alt="Python">
  <img src="https://img.shields.io/badge/rust-1.85+-orange" alt="Rust">
  <img src="https://img.shields.io/badge/RTX%203090-validated-brightgreen" alt="RTX 3090 validated">
  <img src="https://img.shields.io/badge/DGX%20Spark-validated-brightgreen" alt="DGX Spark validated">
  <img src="https://img.shields.io/badge/aarch64-cross_compile_ready-brightgreen" alt="ARM Support">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/honey--comb-compression-orange" alt="honey-comb">
  <img src="https://img.shields.io/badge/rust--brain-memory-brown" alt="rust-brain">
  <img src="https://img.shields.io/badge/busybee--cpu-routing-purple" alt="busybee-cpu">
  <img src="https://img.shields.io/badge/hive--cpp-native_backend-red" alt="hive-cpp">
</p>

**CPU-side control for AI agents.**

Hive reduces agent waste before it reaches the LLM: mechanical tool routing, bloated context, stale memory, and repeated work.

```text
user request
    ↓
busybee-cpu      -> route obvious/mechanical actions on CPU
honey-comb       -> compress agent context while preserving operational signal
rust-brain       -> store causal, timestamp-protected memory
    ↓
LLM inference only when needed
```

## Why Hive exists

Modern agents waste inference on work that should not require a frontier model:

- deciding that the next step is `read_file`, `run_tests`, or `apply_patch`
- dragging huge logs, source files, and stale traces through the context window
- recalling similar-but-wrong vector memories
- repeating failed fixes because old state silently overwrote fresh state

Hive treats those as systems problems, not model problems.

The result is a cheaper and cleaner agent loop: fewer LLM calls, fewer tokens per call, less context pollution, and memory that preserves causal state instead of returning embedding mush.

## Current measured results

| Component | Result | Benchmark level |
|---|---:|---|
| busybee-cpu routing | 2.06M routes/sec on RTX 3090 | macro |
| busybee-cpu routing | 1.73M routes/sec on DGX Spark | macro |
| honey-comb compression | 1.63x context compression | workload |
| honey-comb `rule_fast` | up to 200K messages/sec on RTX 3090 | micro |
| honey-comb `rule_fast` | 19.8K messages/sec on DGX Spark | macro |
| rust-brain memory writes | 270K writes/sec on RTX 3090 | micro |
| rust-brain memory writes | 315K writes/sec on DGX Spark | validated run |
| energy benchmark | 11.2% lower joules/token on measured RTX 3090 workload | measured |

Reproduce the energy benchmark:

```bash
python scripts/energy_benchmark_real.py --prompts 10
```

Raw benchmark artifacts:

- [`docs/benchmarks/latest-macro.json`](docs/benchmarks/latest-macro.json)
- [`docs/benchmarks/latest-micro.json`](docs/benchmarks/latest-micro.json)
- [`docs/benchmarks/README.md`](docs/benchmarks/README.md)
- [`results/energy_real.json`](results/energy_real.json)

Detailed methodology:

- [`docs/benchmarks/README.md`](docs/benchmarks/README.md)
- [`docs/energy.md`](docs/energy.md)
- [`docs/roi.md`](docs/roi.md)

## What Hive is

Hive is not another agent framework.

It is a control layer that sits around an agent runtime and handles the parts of the loop that do not need generative reasoning.

| Layer | Job |
|---|---|
| `busybee-cpu` | Predict and route obvious mechanical actions |
| `honey-comb` | Compress context into agent-critical state |
| `rust-brain` | Preserve timestamped causal memory |
| `hive` | Orchestrate the stack before LLM inference |

## What Hive saves

Hive attacks three sources of waste:

1. **Call waste**: mechanical actions can be routed before invoking the LLM.
2. **Token waste**: logs, files, traces, and search results can be compressed before entering context.
3. **Memory waste**: stale or causally irrelevant memories can be kept out of the prompt.

The marketing-level cost model assumes a deployment where those effects compound into up to **65% lower LLM spend**. The measured system evidence is published separately from the ROI model so users can inspect both.

## Quick start

```bash
git clone https://github.com/DJLougen/hive.git
cd hive
pip install -e .
```

Run tests:

```bash
pytest
```

Run benchmarks:

```bash
python -m hive.scripts.hive_benchmark
python -m hive.scripts.hive_benchmark_micro
python scripts/energy_benchmark_real.py --prompts 10
```

## Minimal usage

```python
from hive import Hive
from busybee_cpu import CpuActionPolicy
from honey_comb import HoneyComb
from hive.rust_brain import RustBrain

policy = CpuActionPolicy.load("runs/my_policy.joblib")
compressor = HoneyComb()
memory = RustBrain()

hive = Hive(
    policy=policy,
    hc=compressor,
    brain=memory,
)

actions = hive.run_session(task="Fix the failing test")
```

## How Hive works

### 1. busybee-cpu: CPU-only action routing

**What it does:** Routes mechanical actions such as `read_file`, `run_tests`, and `apply_patch` to CPU-side policy logic instead of spending an LLM call.

**Why it matters:** A large fraction of agent turns are operational bookkeeping. Hive keeps those decisions out of the model path when they are predictable.

**Performance:**

- RTX 3090: 2.06M routes/sec
- DGX Spark: 1.73M routes/sec

```python
from busybee_cpu import CpuActionPolicy

policy = CpuActionPolicy.load("runs/combined_policy.joblib")
actions = policy.route_batch(states)
```

### 2. honey-comb: Context compression

**What it does:** Compresses agent context while preserving operational signal: failures, file signatures, top search hits, and causal state.

**Why it matters:** The point is not generic summarization. The point is keeping agent-critical state in context while dropping token-heavy noise.

| Message | Raw tokens | Compressed | Ratio | What survives |
|---|---:|---:|---:|---|
| 5000-line test output | 24,107 | 30 | 803x | failure summary + selected failures |
| 50 KB source file | 12,511 | 19 | 658x | file signature + line count |
| Long reasoning | 1,350 | 135 | 10x | first/last signal |
| Search results | 322 | 59 | 5x | top hits |

**Performance:**

- Workload compression: 1.63x
- Macro benchmark: 19.8K to 29K messages/sec, hardware dependent
- Micro benchmark: up to 200K messages/sec for `rule_fast`

```python
from honey_comb import HoneyComb

hc = HoneyComb()
compressed = hc.compress(messages)
```

### 3. rust-brain: Causal memory

**What it does:** Stores agent memories with causal relationships such as `caused_by`, `supersedes`, `related_to`, and `attached_to`.

**Why it matters:** Vector similarity is not enough for agent state. Agents need to know what happened, when it happened, and which state superseded which earlier state.

**Performance:**

- RTX 3090: 270K writes/sec
- DGX Spark: 315K writes/sec

```python
from hive.rust_brain import RustBrain

brain = RustBrain()
brain.remember("test_failure", failure_context, caused_by=["run_tests"])
chain = brain.neighbours("test_failure", edge="caused_by")
```

## Architecture

```text
User Request
    ↓
┌─────────────────────────────────────────────────────────────┐
│                    Hive Orchestrator                        │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │ busybee-cpu  │  │  honey-comb  │  │  rust-brain  │       │
│  │ CPU routing  │  │ compression  │  │ causal memory│       │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘       │
│         └──────────────────┴──────────────────┘             │
│                            ↓                                │
│                     Decide action                           │
└────────────────────────────┬────────────────────────────────┘
                             ↓
                      LLM inference
                    only when needed
```

## Native Backend (Optional)

**hive-cpp** is an optional native Rust implementation that provides significant performance improvements for computationally intensive operations.

### When to Use hive-cpp

Consider `hive-cpp` if you:
- Process large batches of routing decisions
- Need sub-millisecond latency for memory operations
- Run in high-throughput production environments

### Performance Characteristics

When installed, `hive-cpp` automatically accelerates specific operations:

| Operation | Python | hive-cpp | Speedup |
|-----------|--------|----------|---------|
| Routing | ~100ms | 0.37ms | 270x |
| Memory Store | ~100ms | 0.027ms | 3,700x |
| Memory Retrieve | ~100ms | 0.035ms | 2,857x |
| Compression | ~100ms | 0.66ms | 150x |

*PyO3 FFI overhead included (~0.3-0.6ms per call)*

### Installation

```bash
pip install hive-agent-memory[performance]
```

The native backend is optional and only needed for performance-critical workloads.

## Benchmarks

### Macro benchmark, end-to-end

```bash
python -m hive.scripts.hive_benchmark
```

RTX 3090 results:

- Macro runs: 13/13 passed
- busybee routing: 2.06M routes/sec
- honey-comb compression: 29K messages/sec
- rust-brain memory: 174K writes/sec
- LLM simulation: 2,000 tokens/sec

### Micro benchmark, component-level

```bash
python -m hive.scripts.hive_benchmark_micro
```

RTX 3090 results:

- busybee routing: 10.8M routes/sec
- honey-comb compression: 200K messages/sec
- rust-brain memory: 270K writes/sec

### Native Rust backend

```bash
cd hive-cpp
cargo test
cargo bench
```

RTX 4090 results:

- hive: 660 ns/operation, 13x faster than Python
- busybee_cpu: 6,600 ns/route, 3x faster
- honey_comb: 335 ns/compress, 7x faster
- rust_brain: 2,000 ns/remember, 8x faster
- hive_stack: 8,400 ns/step, 7x faster

## Hardware support

| Device | busybee | honey-comb | rust-brain | Status |
|---|---:|---:|---:|---|
| RTX 3090 | 2.06M routes/sec | 28.9K msg/sec macro, 200K msg/sec micro | 270K writes/sec | validated |
| DGX Spark | 1.73M routes/sec | 19.8K msg/sec | 315K writes/sec | validated |
| Grace Hopper | TBD | TBD | TBD | planned |
| Jetson Thor | TBD | TBD | TBD | planned |
| Raspberry Pi 5 | validated | validated | validated | edge deployment |

## Repository structure

```text
hive/
├── README.md
├── pyproject.toml
├── hive/                         # Orchestrator
│   ├── __init__.py
│   ├── stack.py
│   ├── hardware.py
│   ├── llm.py
│   ├── rust_brain/
│   └── rule_fast/
├── busyBee-cpu/                  # CPU routing component
├── honey-comb/                   # Context compression component
├── hive-cpp/                     # Native Rust backend
├── docs/
│   ├── benchmarks/
│   ├── energy.md
│   └── roi.md
└── scripts/
    ├── hive_benchmark.py
    ├── hive_benchmark_micro.py
    └── energy_benchmark_real.py
```

## Development status

| Component | Python | Rust | Status |
|---|---|---|---|
| Orchestrator | complete | skeleton | Step 1 shipped |
| busybee-cpu | complete | complete | Step 1 shipped |
| honey-comb | complete | complete | Step 1 shipped |
| rust-brain | complete | complete | Step 1 shipped |
| Benchmarks | macro + micro | passing | Step 1 shipped |
| Hardware support | RTX 3090, DGX Spark | cross-compile ready | Step 1 shipped |
| Edge deployment | Raspberry Pi 5 | TBD | Step 1 shipped |

## Roadmap

### Step 1: Python meta-package

- 8x8 matrix: 8 components x 8 validations all passing
- Hardware validated: RTX 3090, DGX Spark, Raspberry Pi 5
- Benchmarks: macro and micro suites passing

### Step 2: Native Rust backend

- 4x4 matrix: 4 components x 4 validations all passing
- 3x to 13x speedup vs Python
- PyO3 bindings for Python interop
- Cross-compile: Jetson, Grace, ARM64 ready

### Step 3: LLM integration

- vLLM adapter
- llama.cpp adapter
- OpenAI API adapter
- Local model support

### Step 4: Production hardening

- Monitoring and observability
- Auto-scaling
- Multi-tenant support
- SLA guarantees

## Design principle

The LLM should reason.

The CPU should route, compress, validate, and remember.

Hive makes that split explicit.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development guidelines.

```bash
git clone https://github.com/DJLougen/hive.git
cd hive
pip install -e ".[dev]"
pre-commit install
pytest
```

## License

MIT License. See [LICENSE](LICENSE).

## Citation

```bibtex
@software{hive2025,
  title = {Hive: CPU-side control for AI agents},
  author = {Lougen, DJ},
  year = {2025},
  url = {https://github.com/DJLougen/hive}
}
```

## Related projects

- [BusyBeaver-50M](https://github.com/DJLougen/BusyBeaver-50M): Dataset for training CPU routing policies
- [busybee-cpu](https://github.com/DJLougen/busyBee-cpu): CPU-side routing primitive
- [Rust-Brain](https://github.com/DJLougen/Rust-Brain): Durable causal memory

## Bottom line

Stop paying frontier-model prices for bookkeeping.

Hive moves routing, compression, validation, and memory management into a CPU-side control layer so the model spends tokens only when reasoning is actually needed.
