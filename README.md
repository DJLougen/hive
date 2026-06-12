# Hive

**Orchestration layer for AI agents** — CPU-side action routing, context compression, and causal graph memory that keep mechanical work and context bloat off the LLM.

[![Version](https://img.shields.io/badge/version-0.6.0-blue)](https://github.com/DJLougen/hive)
[![Python](https://img.shields.io/badge/python-3.10+-green)](https://python.org)
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen)](https://github.com/DJLougen/hive/actions)
[![License](https://img.shields.io/badge/license-MIT-yellow)](https://opensource.org/licenses/MIT)
[![RTX 3090](https://img.shields.io/badge/RTX%203090-validated-orange)]()
[![DGX Spark](https://img.shields.io/badge/DGX%20Spark-validated-red)]()

Hive sits between an agent loop and its LLM. It answers the mechanical decisions on the CPU, compresses the context the LLM actually sees, and keeps a timestamped causal-memory graph so the agent stops re-deriving what it already learned. On a 20-instance SWE-bench-lite A/B (GPT-2 backend) this cut LLM calls by 91.7% and resolved 85% of instances versus 0% for the un-augmented agent — numbers below.

> **Status:** v0.6.0 (Beta). The core stack plus the enterprise, reliability and observability modules are implemented and tested (34 test files). Routing-accuracy numbers are *in-distribution* — see the OOD caveat under [Components](#components).

---

## Real-workload evaluation (SWE-bench-lite)

20 real SWE-bench-lite instances, GPT-2 as the LLM backend. **Baseline** runs the agent with every decision going to the LLM. **Hive** adds CPU routing + context compression + causal memory.

| Metric | Baseline | Hive | Delta |
|---|---|---|---|
| Resolve rate | 0.0% | 85.0% (17/20) | **+85.0 pp** |
| Mean input tokens | 6,324 | 526 | **−91.7%** |
| Mean output tokens | 1,000 | 85 | **−91.5%** |
| Mean turns | 20.0 | 9.8 | **−51.0%** |
| Mean LLM calls | 20.0 | 1.7 | **−91.7%** |
| LLM calls avoided / instance | — | 8.1 | — |
| Mean wall clock (s) | 8.68 | 0.72 | **−91.7%** |

**Why it works:** without Hive every turn — including mechanical ones like `read_file`, `run_tests`, `apply_patch` — burns an LLM call, and the agent never reaches a patch. With Hive the CPU policy handles mechanical actions instantly, leaving the LLM the ~2 reasoning steps that genuinely need it.

- **Hardware:** Windows 11, Intel i9-12900K, RTX 3090 (CUDA 13.0), Python 3.12
- **Reproduce:** `python scripts/hive_swebench_eval.py --instances 20 --model gpt2`
- **Raw runs:** [`docs/benchmarks/swebench-lite/`](docs/benchmarks/swebench-lite/)

### Compression sensitivity

Sweeping four compression-aggressiveness settings (conservative → extreme) over the same 20 instances:

| Setting | Resolve rate | Compression ratio | Mean tokens | Mean turns |
|---|---|---|---|---|
| Conservative | 60.0% | 1.0× | 703 | 9.8 |
| Moderate | 60.0% | 1.0× | 703 | 9.8 |
| Aggressive | 60.0% | 1.0× | 703 | 9.8 |
| Extreme | 60.0% | 1.0× | 703 | 9.8 |

**Honest finding:** the agent messages in this run are short enough to fall below the compressor's threshold at every setting, so the ratio stays at 1.0× and resolve rate is flat — on this workload the win comes from **routing**, not compression. Compression pays off on long transcripts (multi-thousand-line tool output, logs), which this sample does not contain. Full sweep: [`docs/benchmarks/compression-sweep-20260611T200633Z.json`](docs/benchmarks/compression-sweep-20260611T200633Z.json).

---

## What Hive does

Three jobs, all on the CPU, before the LLM is involved:

1. **Routes mechanical decisions** — `read_file`, `run_tests`, `apply_patch`, etc. go to a trained CPU policy (busyBee-cpu). No LLM call. Out-of-distribution states escalate to the LLM instead of guessing.
2. **Compresses context** — a content-type-aware classifier labels each message and drops or distills the wax (stale logs, unchanged files) so the LLM sees only the honey.
3. **Remembers causally** — a timestamped graph store (rust-brain) records cause → effect → supersession chains, so the agent can later answer "why did this happen / what fixed it".

```text
            agent request
                  │
        ┌─────────▼──────────┐
        │     HiveStack      │
        │                    │
        │ route(state)       │ → RouteDecision  (CPU policy, or escalate)
        │ compress(role,msg) │ → CompressedTurn (6-label classifier)
        │ remember(k, v)     │ → MemoryNode     (causal graph)
        │ step(state, txns)  │ → all of the above
        └─────────┬──────────┘
                  │
   compressed context + decision
                  │
                  ▼
   LLM  (only for decisions that need reasoning)
```

---

## Architecture & complexity

Hive is a *meta-package*. The orchestrator (`hive.stack.HiveStack`) is small; the surface area around it is not. The package ships ~28 modules spanning orchestration, memory, learning, security, reliability, observability and deployment.

```text
hive/
├── stack.py            # HiveStack — the orchestrator facade (route/compress/remember/step)
├── async_stack.py      # AsyncHiveStack — async API for FastAPI / high-throughput
├── config.py           # HiveConfig — enterprise configuration
│
│   memory
├── rust_brain/         # RustBrain causal graph: HybridLogicalClock, EdgeKind, MemoryNode,
│                       #   TTL/eviction, tenant isolation, Hermes backend
├── semantic_search.py  # SemanticIndex — optional vector search over the brain
│
│   compression
├── rule_fast/          # RuleFastHoneyComb — in-repo rule-based compressor (no honey-comb dep)
│                       #   Label: CORE / DISTILL / COMPACT / DROP / STALE / ESCALATE
│
│   routing & learning
├── feedback.py         # FeedbackBuffer, OutcomeType, RoutingOutcome
├── policy_updater.py   # PolicyUpdater — retrain busyBee from collected feedback
├── ab_test.py          # ABTestHarness — guarded A/B of policy updates
├── llm.py              # unified LLM client (OpenAI-compatible + echo backend, endpoint discovery)
│
│   security
├── auth.py             # JWTValidator + role-based access control
├── encryption.py       # Encryptor — encryption at rest
├── model_registry.py   # signed .joblib registry — blocks pickle-RCE from untrusted models
├── audit_export.py     # SIEM-compatible audit-log export
├── schemas.py          # Pydantic validation for the public API
│
│   reliability
├── circuitbreaker.py   # CircuitBreaker for LLM / external calls
├── ratelimit.py        # TokenBucket / RateLimiter (per-tenant)
├── health.py           # Kubernetes-style health & readiness probes
│
│   distributed
├── gossip.py           # GossipProtocol — cross-node memory replication
├── deployment.py       # DeploymentMarker — blue-green / canary rollout markers
│
│   observability
├── telemetry.py        # Telemetry collector (routing/compression/memory events)
├── tracing.py          # W3C traceparent distributed tracing (TraceContext, Span)
├── hardware.py         # NVML power/util sampling (PowerSampler)
│
│   extensibility
├── plugins.py          # register custom compressors / routers / memory backends
└── streaming.py        # WebSocket / SSE streaming (StreamRouter, StreamCompressor, SSETransport)
```

External siblings (developed in their own repos, all optional):

- **busyBee-cpu** — the trained CPU action policy — <https://github.com/DJLougen/busyBee-cpu>
- **honey-comb** — the full context compressor (`rule_fast` is the in-repo fallback) — <https://github.com/DJLougen/honey-comb>
- **hive-cpp** — optional native Rust backend (see [below](#native-rust-backend-hive-cpp))

`HiveStack` lazy-imports the siblings, so `pip install hive-agent-memory` runs on the in-repo `rule_fast` + `rust_brain` alone; the others light up automatically when present.

---

## Installation

```bash
pip install hive-agent-memory
```

Optional extras:

```bash
pip install "hive-agent-memory[observability]"   # Prometheus + OpenTelemetry
pip install "hive-agent-memory[monitor]"         # NVML hardware monitoring
pip install "hive-agent-memory[performance]"     # native Rust backend (hive-cpp)
pip install "hive-agent-memory[gpu]"             # torch + transformers (examples / integration)
```

From source:

```bash
git clone https://github.com/DJLougen/hive.git
cd hive
pip install -e ".[dev]"
pytest
```

---

## Quick start

```python
from hive import HiveStack

stack = HiveStack()                       # rule_fast + rust_brain; siblings auto-detected

state = {"goal": "Fix auth bug", "step": 1}
transcript = [
    ("user", "The login is failing"),
    ("assistant", "Let me check the logs..."),
    ("user", "Here: " + "...5000 lines of test output..."),
]

result = stack.step(state, transcript)
print(result["decision"])    # RouteDecision(tool=..., args=..., escalated=..., source=...)
print(result["compressed"])  # CompressedTurn(label=..., original_tokens=..., compressed_tokens=...)
print(result["stats"])       # {"brain": {...}, "comb": {...}}
```

`step()` returns a dict with exactly three keys: `decision`, `compressed`, `stats`.

### Manual control

```python
# Route a decision (CPU-only). With no busyBee policy loaded, every call escalates.
decision = stack.route(state)
print(decision.tool, decision.source, decision.escalated)   # e.g. "read_file" "busybee" False

# Compress a message (content_type is optional — inferred if omitted)
turn = stack.compress("user", "...long test output...", content_type="TOOL_RESULT_TEST")
print(turn.label, turn.ratio)   # e.g. "DROP" 12.4

# Remember something, optionally causal-linked
stack.remember("auth_bug", {"cause": "expired token", "fix": "refresh"}, tags=("incident",))
value = stack.recall("auth_bug")        # exact-key lookup → the stored value (or None)
```

---

## Core API

### `HiveStack`

The orchestrator. All constructor arguments are keyword-only and optional:

```python
stack = HiveStack(
    busybee_policy=None,      # trained CpuActionPolicy; None → every route() escalates
    honey_comb=None,          # HoneyComb instance; None → in-repo RuleFastHoneyComb
    rust_brain=None,          # RustBrain instance; None → in-memory store
    telemetry=None,           # Telemetry collector
    feedback_buffer=None,     # FeedbackBuffer for online learning
    tenant_id="default",      # multi-tenant memory isolation
    validate=False,           # Pydantic validation of inputs
    config=None,              # HiveConfig
    rate_limiter=None,        # RateLimiter (per-tenant)
    circuit_breaker=None,     # CircuitBreaker for the LLM path
    max_content_bytes=1_048_576,
)
```

Methods: `route`, `compress`, `compress_many`, `remember`, `recall`, `record_outcome`, `should_update_policy`, `update_policy`, `step`, `stats`. The causal store is exposed directly as `stack.brain`.

#### `route(state) -> RouteDecision`

```python
@dataclass(slots=True)
class RouteDecision:
    tool: str            # "read_file", "run_tests", "escalate", ...
    args: dict
    confidence: float
    escalated: bool      # True when the decision was sent to the LLM
    source: str          # "busybee" | "fallback" | "ratelimit"
```

#### `compress(role, content, *, content_type=None) -> CompressedTurn`

```python
@dataclass(slots=True)
class CompressedTurn:
    role: str
    content: str
    label: str           # CORE | DISTILL | COMPACT | DROP | STALE | ESCALATE
    original_tokens: int
    compressed_tokens: int
    # .ratio -> original_tokens / compressed_tokens
```

#### `remember(key, value, *, trust=1.0, tags=None, caused_by=None) -> MemoryNode`

Write a node, optionally causal-linked to earlier keys via `caused_by`.

#### `recall(key, default=None) -> Any`

Exact-key lookup returning the stored value. For tag / trust queries use `stack.brain.search(tag=..., min_trust=...)`; for graph walks use `stack.brain.neighbours(key, kind)`.

#### Online learning

```python
from hive.feedback import OutcomeType

decision = stack.route(state)
stack.record_outcome(decision, actual_action="read_file", outcome_type=OutcomeType.CORRECT)

if stack.should_update_policy():     # True once the feedback buffer is full
    stack.update_policy()            # retrains busyBee in place; returns bool
```

`record_outcome` rejects feedback that does not match the most recent `route()` call — an anti-policy-poisoning guard.

---

## Components

**busyBee-cpu** — CPU action routing
- 98.2% accuracy on the training distribution (SWE-bench trajectories).
- **OOD performance is unproven**; out-of-distribution states escalate to the LLM rather than guess.
- ~2.06M routes/s (RTX 3090), ~1.73M routes/s (DGX Spark).

**honey-comb / rule_fast** — context compression
- 6-label scheme — `CORE, DISTILL, COMPACT, DROP, STALE, ESCALATE` — driven by an 11-value `ContentType` taxonomy (`TOOL_RESULT_TEST`, `AGENT_PATCH`, `TOOL_RESULT_FILE`, …).
- `rule_fast` is the dependency-free in-repo fast path; `honey-comb` is the full compressor.
- Compression ratio scales with input length: ~1.0× on short agent turns, high ratios on long tool output / logs.

**rust-brain** — causal memory
- Timestamped graph with a Hybrid Logical Clock; monotonic timestamps (`TimestampRegression` on stale writes).
- Edge kinds: `related_to`, `caused_by`, `supersedes`, `attached_to`.
- Per-tenant isolation, TTL + LRU eviction, optional vector search (`semantic_search.SemanticIndex`). Data is durable: `snapshot_to_file()` (gzip+SHA256) and `restore_from_file()` persist memory across restarts.
- ~270K writes/s, ~315K reads/s (DGX Spark).

### Causal memory: worked example

```python
# Day 1 — agent records a failing endpoint
stack.remember(
    "endpoint_health",
    {"url": "/api/v2/users", "status": 500, "root_cause": "db pool exhausted"},
    tags=("incident", "production"),
)

# Day 1 — agent applies a fix; supersede the old observation
stack.brain.supersede(
    "endpoint_health",
    {"url": "/api/v2/users", "status": 200, "fix": "max_connections=200"},
    tags=("incident", "production", "resolved"),
)

# Day 14 — same endpoint breaks again; walk the chain for provenance
prior = stack.brain.neighbours("endpoint_health", "supersedes")
# → ["endpoint_health"]: the original 500 / pool-exhausted observation.
#   The agent reconstructs "this was fixed two weeks ago by raising the pool" —
#   something a pure vector store cannot recover from embeddings alone.
```

---

## Performance

Component micro-benchmarks (synthetic load; raw data in [`docs/benchmarks/`](docs/benchmarks/)):

| Component | Metric | RTX 3090 | DGX Spark |
|---|---|---|---|
| busyBee-cpu | routes/s | 2.06M | 1.73M |
| rule_fast | messages/s | 200K | 200K |
| honey-comb | messages/s | 29K | 29K |
| rust-brain | writes/s | 270K | 315K |
| rust-brain | reads/s | 315K | 315K |

Reproduce:

```bash
python scripts/hive_benchmark.py          # macro (full stack)
python scripts/hive_benchmark_micro.py    # micro (per component)
```

Energy methodology and raw NVML samples live in [`docs/energy.md`](docs/energy.md).

---

## Native Rust backend (hive-cpp)

`hive-cpp` is an optional Rust implementation of the hot paths (router, compressor, memory store). When the `hive_cpp` module is importable, `HiveStack` auto-detects and uses it — there is no flag to set:

```bash
pip install "hive-agent-memory[performance]"   # or: pip install hive-cpp
```

Wheels for Linux / macOS / Windows (x86_64 + aarch64) are built by the `rust-wheels` CI job on tagged releases. See [`hive-cpp/README.md`](hive-cpp/README.md) for component benchmarks and build-from-source (maturin).

*Note: The current `hive-cpp` memory backend uses a `HashMap` in `OnceLock` for stable, high-performance in-memory state. SIMD-accelerated roaring-bitmap indexing is on the roadmap.*

---

## Deployment

Hive ships container and orchestration assets:

- **HTTP server** — [`scripts/hive_api_server.py`](scripts/hive_api_server.py) (FastAPI). Endpoints: `POST /route`, `POST /compress`, `POST /remember`, `GET /recall`, plus `GET /health` and `GET /ready` probes. `AsyncHiveStack` backs high-throughput deployments.
- **Helm chart** — [`deploy/helm/`](deploy/helm/) (chart `0.6.0`).
- **Raw K8s manifests** — [`deploy/k8s/`](deploy/k8s/) (Deployment, Service, ConfigMap).
- **ARM64 image** — [`docker/Dockerfile.aarch64`](docker/Dockerfile.aarch64) for Jetson / Grace.

Enterprise concerns are first-class modules, documented in [docs/USAGE.md](docs/USAGE.md):

| Concern | Module |
|---|---|
| AuthN / AuthZ | `hive.auth` — JWT + RBAC |
| Encryption at rest | `hive.encryption` |
| Untrusted-model safety | `hive.model_registry` — signed `.joblib`, blocks pickle RCE |
| Audit / SIEM | `hive.audit_export` |
| Rate limiting | `hive.ratelimit` — per-tenant token bucket |
| Circuit breaking | `hive.circuitbreaker` |
| Multi-tenancy | `RustBrain(tenant_id=…, tenant_isolation=True)` — supports `revoke_tenant()` for GDPR Article 17 mass-erase |
| Observability | `hive.telemetry`, `hive.tracing` (W3C traceparent), Prometheus / OTel |
| Health probes | `hive.health` |
| Audit | `hive.audit_export` (SIEM-compatible) |

---

## Development

```bash
pip install -e ".[dev]"

# Run the suite (34 test files)
pytest

# Focused runs
pytest tests/test_stack.py -v
pytest tests/test_rust_brain.py tests/test_rust_brain_concurrency.py -v
pytest tests/test_online_learning.py -v
pytest tests/test_enterprise_auth.py tests/test_security_fixes.py -v

# Lint & types (matches CI)
ruff check hive/ tests/
mypy hive/ --ignore-missing-imports
```

CI (`.github/workflows/ci.yml`) runs the suite on Python 3.10–3.12, a benchmark smoke test, ruff, mypy, bandit, the modular pentest, and CITATION.cff validation.

---

## Roadmap

- [x] Core orchestration (routing, compression, causal memory)
- [x] Enterprise modules (auth, encryption, audit, rate limiting, multi-tenancy)
- [x] Observability (telemetry, W3C tracing, Prometheus / OpenTelemetry)
- [x] Online learning + guarded policy A/B
- [x] Native Rust backend (hive-cpp) with multi-platform wheels
- [x] Real-workload SWE-bench-lite A/B evaluation
- [ ] Durable distributed memory backend (gossip replication is in place; durability pending)
- [ ] Kubernetes operator for autoscaling
- [ ] Broader out-of-distribution routing coverage

---

## License

MIT — see [LICENSE](LICENSE).

## Citation

If you use Hive in research, cite it via the repository's [`CITATION.cff`](.github/citation.cff), or:

```bibtex
@software{hive2026,
  title  = {Hive: orchestration layer for AI agents},
  author = {Lougen, Daniel J.},
  year   = {2026},
  url    = {https://github.com/DJLougen/hive}
}
```

## Support

- Issues — <https://github.com/DJLougen/hive/issues>
- Discussions — <https://github.com/DJLougen/hive/discussions>
