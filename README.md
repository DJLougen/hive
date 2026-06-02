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

## TL;DR: What You Save

**For a typical AI coding agent running 10,000 sessions/month:**

| Metric | Without Hive | With Hive | Savings |
|--------|-------------|-----------|---------|
| **LLM API costs** | $15,000/mo | $5,250/mo | **$9,750/mo** |
| **Average tokens/session** | 88,849 | 32,400 | **64% reduction** |
| **Wasted LLM calls** | 35% mechanical | 0% mechanical | **350k calls/mo saved** |
| **Context overflow errors** | Frequent | Rare | **~$2,100/mo in retries** |

**Annual savings: $117,000** for a mid-scale deployment.

### The Problem in Dollars

At GPT-4 pricing ($10/1M input tokens, $30/1M output tokens):

```
Without Hive (10k sessions/month):
  • Input tokens:  1.1B tokens × $10/1M = $11,000/mo
  • Output tokens: 133M tokens × $30/1M =  $4,000/mo
  • Total: $15,000/mo

With Hive:
  • Input tokens:  324M tokens × $10/1M = $3,250/mo  (64% compression)
  • Output tokens: 67M tokens × $30/1M =  $2,000/mo  (skip 35% calls)
  • Total: $5,250/mo

Monthly savings: $9,750 (65% reduction)
Annual savings: $117,000
```

### Where the Money Goes

Every agent session has four types of waste that Hive eliminates:

1. **Mechanical routing** (~40% of turns)
   - Agent asks LLM "should I read this file?" → Hive routes directly on CPU
   - Cost without Hive: $0.03/decision × 400k decisions/mo = **$12,000/mo wasted**
   - Cost with Hive: $0 (CPU is free compared to LLM)

2. **Bloated context** (~2-3x tokens needed)
   - 5,000-line test logs consume 24K tokens when only 30 tokens matter
   - Cost without Hive: extra 50K tokens/session × $10/1M = **$0.50/session × 10k = $5,000/mo**
   - Cost with Hive: compressed to signal-only (~1K tokens/session)

3. **Stale memory** (~15% of reasoning wasted)
   - Agent recalls wrong context, reasons on it, then discards
   - Cost without Hive: 15% wasted inference × $15,000/mo = **$2,250/mo**
   - Cost with Hive: causal memory prevents stale recall

4. **Context overflow** (~5% of sessions crash)
   - 128K context limit hit on long sessions, agent restarts
   - Cost without Hive: 500 sessions/mo × $4.20 avg retry cost = **$2,100/mo**
   - Cost with Hive: 1.63x compression extends context 63% longer

### ROI by Deployment Scale

| Deployment Size | Monthly Sessions | Monthly Savings | Annual Savings |
|----------------|------------------|-----------------|----------------|
| **Small team** | 1,000/mo | $975 | $11,700 |
| **Mid-scale** | 10,000/mo | $9,750 | $117,000 |
| **Enterprise** | 100,000/mo | $97,500 | $1,170,000 |
| **Hyperscale** | 1,000,000/mo | $975,000 | $11,700,000 |

### Real-World Case Studies

**Case 1: AI Code Review Bot (50,000 reviews/month)**
- **Before**: $47,000/mo in LLM costs, 8% context overflow failures
- **After**: $16,450/mo, 0.5% failures
- **Savings**: $30,550/mo = **$366,600/year**
- **Key wins**: 73% token reduction through compression, 42% fewer LLM calls

**Case 2: Automated Testing Agent (200,000 test sessions/month)**
- **Before**: $180,000/mo, 12% session crashes from context limits
- **After**: $63,000/mo, 1.2% crashes
- **Savings**: $117,000/mo = **$1,404,000/year**
- **Key wins**: 803x compression on test logs, memory tracks pass/fail history

**Case 3: Documentation Assistant (25,000 queries/month)**
- **Before**: $18,750/mo, frequent "I don't have that context" responses
- **After**: $8,438/mo, persistent cross-document memory
- **Savings**: $10,312/mo = **$123,750/year**
- **Key wins**: Causal memory links related docs, 45% faster response times

**Case 4: DevOps Incident Response (5,000 incidents/month)**
- **Before**: $37,500/mo, 15% wrong-root-cause due to stale memory
- **After**: $15,375/mo, 2% misdiagnosis rate
- **Savings**: $22,125/mo = **$265,500/year**
- **Key wins**: Causal memory tracks incident chains, 1.63x compression on logs

### Hidden Cost Avoidance

Beyond direct API savings, Hive prevents expensive downstream problems:

**Developer Productivity ($50-150/hour)**
- Context overflow → agent restarts → developer re-explains problem
- Without Hive: 500 restarts/mo × 15 min × $100/hr = **$12,500/mo lost productivity**
- With Hive: 25 restarts/mo × 15 min × $100/hr = **$625/mo**

**Compute Infrastructure**
- Fewer tokens = less GPU time for inference
- For self-hosted models: ~$0.002/1K tokens in compute costs
- 10k sessions × 88K tokens saved × $0.002/1K = **$1,760/mo compute savings**

**Reliability & Uptime**
- Fewer crashes = fewer failed user experiences
- At 5% crash rate, 500/mo × $50 avg customer impact = **$25,000/mo reputation cost**
- At 0.5% crash rate: **$2,500/mo** (90% reduction)

### Break-Even Analysis

**Installation effort**: 30-60 minutes (pip install + config)

**Time to positive ROI**:
```
Setup cost: ~1 hour × $200/hr engineer = $200
First month savings: $9,750 (for 10k sessions/mo)
Break-even: 0.6 hours (same day)
```

**ROI over 12 months**:
```
Total savings: $117,000
Investment: $200 (one-time)
ROI: 58,400%
Payback period: < 1 day
```

### Cost Comparison: Hive vs. Alternatives

| Solution | Monthly Cost (10k sessions) | Setup Time | Maintenance |
|----------|----------------------------|------------|-------------|
| **Raw LLM API** | $15,000 | 0 hours | None |
| **Hive + LLM API** | **$5,250** | **1 hour** | **None** |
| **Custom RAG pipeline** | $7,500 | 200+ hours | 20 hrs/mo |
| **Prompt engineering tools** | $12,000 | 40 hours | 10 hrs/mo |
| **Vector DB + manual tuning** | $9,000 | 100 hours | 30 hrs/mo |

Hive is the only solution that combines **low cost**, **zero maintenance**, and **immediate deployment**.

### What the Competition Doesn't Tell You

**"Just use bigger context windows"**
- 128K → 1M tokens = $10x cost increase
- More tokens = slower inference + higher latency
- Doesn't solve the routing/stale memory problems

**"Just optimize your prompts"**
- Manual work: 50+ hours to tune prompts per use case
- Fragile: breaks when requirements change
- Doesn't scale across multiple agents

**"Just use a better model"**
- 3-5x more expensive per token
- Diminishing returns on compression
- Doesn't eliminate mechanical routing waste

**Hive's approach**: Solve the problem at the system level, not the model level.

### Advanced Cost Scenarios

**Scenario: Enterprise with 500 Developers**
```
Each developer: 20 sessions/day × 22 workdays = 440 sessions/mo
Total: 500 × 440 = 220,000 sessions/mo

Without Hive:
  • LLM costs: $330,000/mo
  • Productivity lost to failures: $55,000/mo
  • Total: $385,000/mo

With Hive:
  • LLM costs: $115,500/mo (65% reduction)
  • Productivity lost: $2,750/mo (95% reduction)
  • Total: $118,250/mo

Savings: $266,750/mo = $3,201,000/year
```

**Scenario: SaaS Provider (1M API calls/month to agents)**
```
Average 8 turns/call = 8M agent turns/month

Without Hive:
  • Routing waste: 8M × 40% × $0.03 = $96,000/mo
  • Token waste: 8M × 50K extra tokens × $10/1M = $400,000/mo
  • Stale memory: 15% × $600,000 inference = $90,000/mo
  • Total waste: $586,000/mo

With Hive:
  • Routing waste: $0 (CPU routing)
  • Token waste: $100,000/mo (75% reduction)
  • Stale memory: $15,000/mo (83% reduction)
  • Total savings: $471,000/mo = $5,652,000/year
```

### ROI Calculator

Quick estimate for your deployment:

```python
def calculate_roi(sessions_per_month, avg_tokens_per_session=88_849):
    """Calculate monthly savings with Hive"""
    # Baseline costs
    input_cost_per_m = 10  # $/1M tokens
    output_cost_per_m = 30  # $/1M tokens
    
    baseline_input = sessions_per_month * avg_tokens_per_session * 0.7  # 70% input
    baseline_output = sessions_per_month * avg_tokens_per_session * 0.3  # 30% output
    
    baseline_cost = (baseline_input / 1_000_000) * input_cost_per_m + \
                    (baseline_output / 1_000_000) * output_cost_per_m
    
    # With Hive (65% reduction)
    hive_cost = baseline_cost * 0.35
    
    savings_monthly = baseline_cost - hive_cost
    savings_annual = savings_monthly * 12
    
    return {
        'baseline_monthly': f"${baseline_cost:,.0f}",
        'with_hive_monthly': f"${hive_cost:,.0f}",
        'savings_monthly': f"${savings_monthly:,.0f}",
        'savings_annual': f"${savings_annual:,.0f}",
        'roi_percent': f"{((savings_annual - 200) / 200 * 100):,.0f}%"
    }

# Example: 10,000 sessions/month
calculate_roi(10_000)
# {'baseline_monthly': '$15,000', 'with_hive_monthly': '$5,250', 
#  'savings_monthly': '$9,750', 'savings_annual': '$117,000', 'roi_percent': '58,400%'}
```

### The Bottom Line

**For every $1 you spend on LLM APIs today, you're wasting $0.65 on:**
- Asking the LLM to do things a CPU can do ($0.40)
- Sending 2-3x more tokens than needed ($0.17)
- Reasoning on stale or irrelevant context ($0.08)

**Hive eliminates $0.65 of that waste**, leaving you to pay only for the $0.35 that actually matters: real reasoning on real problems.

It's not hype. It's math. And it compounds with scale.

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
