# Hive

**Orchestration layer for AI agents** — CPU-side action routing, context compression, and causal graph memory that keep mechanical work and context bloat off the LLM.

[![Version](https://img.shields.io/github/v/release/DJLougen/hive?label=release)](https://github.com/DJLougen/hive/releases/latest)
[![Python](https://img.shields.io/badge/python-3.10+-green)](https://python.org)
[![Tests](https://github.com/DJLougen/hive/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/DJLougen/hive/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-yellow)](https://opensource.org/licenses/MIT)
[![RTX 3090](https://img.shields.io/badge/RTX%203090-validated-orange)]()
[![DGX Spark](https://img.shields.io/badge/DGX%20Spark-validated-red)]()

Hive sits between an agent loop and its LLM. It answers the mechanical decisions on the CPU, compresses the context the LLM actually sees, and keeps a timestamped causal-memory graph so the agent stops re-deriving what it already learned. On a 10-task real tool-execution benchmark (real repos, real pytest gate, DeepSeek-V4.1-Flash as the LLM) this cut LLM calls by 83% and prompt tokens by 82% at an identical 30/30 resolve rate — numbers below.

> **Status:** v0.6.1 (Beta). The August 2026 modernization ([PR #62](https://github.com/DJLougen/hive/pull/62)), HLC preservation (PRs #71–#87), and the review-driven fixes ([PR #88](https://github.com/DJLougen/hive/pull/88)–[PR #91](https://github.com/DJLougen/hive/pull/91)) are merged on `main` — full suite green on Python 3.10–3.13 locally and in CI. Routing-accuracy numbers are *in-distribution* — see the OOD caveat under [Components](#components). **PFN / busyBee-cpu training-mode integration** is in progress (see [busyBee-cpu](https://github.com/DJLougen/busyBee-cpu)).

## What's new (August 2026)

Four-tier modernization, validated locally and in CI on Python 3.10–3.13. Full detail: [`docs/WHATS_NEW.md`](docs/WHATS_NEW.md) · [CHANGELOG](CHANGELOG.md#unreleased).

| Outcome | What shipped |
|---|---|
| **Logical clocks (HLC)** | Memory tracks event order and cause-and-effect, not just wall-clock time — ordering stays correct when messages arrive late; snapshot restore and gossip replay preserve `hlc`/`ts_ns` |
| **Review fixes** | `validate=True` actually validates; `supersede()` chains stay walkable via bounded `history()`; Ed25519 model signatures; stdlib JWKS fetch; bearer-token auth for gossip and the REST API (`HIVE_API_TOKEN`) |
| **Gossip + audit wiring** | `HiveStack(gossip=…)` publishes every `remember()` to peers; `HiveConfig(audit_enabled=True)` keeps a bounded audit trail via `stack.audit_events()` for SIEM export |
| **MCP server** | `pip install "hive-agent-memory[agents]"` → `hive-mcp` console command; project config at `.cursor/mcp.json`; setup for Cursor, Claude Desktop, and Codex via [docs/MCP_SETUP.md](docs/MCP_SETUP.md) |
| **Long-context proof** | `python scripts/hive_long_context_eval.py --smoke` — up to **153.8×** compression on 50k+ char synthetic logs, measured in [`docs/benchmarks/long-context-smoke.json`](docs/benchmarks/long-context-smoke.json) (short agent turns stay near 1×; routing is the win there) |
| **`HIVE_BACKEND`** | `python` \| `native` \| `auto` — `auto` (the default) stays on the Python reference implementation; native is opt-in |
| **LinUCB** | sklearn-free contextual bandit in `hive.policy_updater` for online routing updates |
| **Async LLM** | `httpx`-backed `_OpenAICompatBackend.achat` (`[http]` extra) |
| **Dev + supply chain** | `uv.lock`, pre-commit, Dependabot, ruff, pip-audit + SBOM CI, Docker L4T r36.4.0 bump |

*Not in this release:* PFN-based busyBee training mode — tracked on the [roadmap](#roadmap).

---

## Real-workload evaluation (hive-bench)

10 real bug-fix tasks ([`benchmarks/tasks/`](benchmarks/tasks/)), 3 repeats each — each is a real repo with a real failing pytest suite. The agent acts through real tools (`list_files`, `read_file`, `grep`, `run_tests`, `write_file`, `finish`) executed by the harness; `tests/` is read-only so a pass can't be gamed. Resolve = a real `pytest` run at the end of the episode. **Baseline** sends every action decision to the LLM. **Hive** routes mechanical transitions through the CPU policy, compresses tool observations, and recalls prior fixes from causal memory. Same model (`deepseek-v4p1-flash`, Fireworks), same tools, same prompts, `temperature=0`.

**Which router produced this table, and how repeatable it is:** the Hive arm runs the built-in rule-based state machine (`hive.harness.RuleBasedRoutingPolicy`, the `--policy rule` default) driving harness-computed read hints — *not* the trained `CPURouterPolicy`. The artifact records that itself (`provenance`: `policy_class`, `policy`, `temperature`, `repeat`, git sha). The table is **3 repeats × 10 tasks = 30 episodes per arm** (not one pass), and per-pass means are published alongside it: baseline LLM calls 5.9 / 6.1 / 6.0 (stderr 0.06), Hive 1.0 / 1.0 / 1.0 (stderr 0.00). The trained router's own numbers are in [`docs/benchmarks/hive-bench-cpu-policy.json`](docs/benchmarks/hive-bench-cpu-policy.json) (pass 0 = 1.8 mean LLM calls, i.e. *more* model calls than the table above, because it escalates more) — read the table as "rule engine + orchestration + memory", and that artifact as the trained-router result.

| Metric | Baseline | Hive | Delta |
|---|---|---|---|
| Resolve rate | 100% (30/30) | 100% (30/30) | **0 pp** |
| Mean LLM calls | 6.0 | 1.0 | **−83.3%** |
| Mean prompt tokens | 8,590 | 1,534 | **−82.1%** |
| Mean completion tokens | 441 | 227 | **−48.5%** |
| Mean turns | 6.0 | 7.0 | +16.7% |
| Mean wall clock (s) | 8.94 | 2.80 | **−68.7%** |
| Memory recall hits | — | 24/30 | — |

**Why it works:** each episode runs ~6 mechanical turns (`list_files`, reproduce `run_tests`, read the file the traceback names, verify `run_tests`, `finish`). Without Hive every one of those is a paid LLM call with the full transcript attached. With Hive the CPU policy executes them locally; the model is called once — with the failing test output and the unit under test already in context — and writes the patch. Resolve rate is unchanged because the reasoning still goes to the same model.

- **Reproduce:** `python scripts/hive_bench.py --backend openai --endpoint <openai-compatible-url> --api-key-env <KEY> --model <model>`
- **Raw run (this table):** [`docs/benchmarks/hive-bench-flash-r3.json`](docs/benchmarks/hive-bench-flash-r3.json) — 30 episodes per arm, provenance and per-pass dispersion recorded. The earlier single-pass run is kept at [`docs/benchmarks/hive-bench-flash.json`](docs/benchmarks/hive-bench-flash.json) and is superseded by this one.

**Learned CPU policy + memory replay.** `hive.cpu_policy.CPURouterPolicy` is a RandomForest trained by imitating logged trajectories (`--log`, `scripts/train_cpu_policy.py`); it predicts mechanical tool calls on the CPU and escalates anything it can't resolve safely — including `write_file` without a recalled fix, since patch synthesis isn't a routing decision. With `--policy trained --repeat 2` the second pass replays the fix stored in causal memory: `write_file -> run_tests -> finish`, all CPU-routed. Measured: pass 0 = 10/10 resolved at 1.8 mean LLM calls; pass 1 = 10/10 at **0 LLM calls / 0 tokens** ([`docs/benchmarks/hive-bench-cpu-policy.json`](docs/benchmarks/hive-bench-cpu-policy.json)).

**Where the boundary is — measured on real agent traces.** `benchmarks/traces/` + `scripts/trace_bench.py` evaluate the same policy on 100 deidentified tool-call sequences from real omp/prime-agent sessions (5 workflow families), training on a separate 1,595-trace pool (~148k decisions). An 8-algorithm bake-off (rf, rf-deep, extratrees, hgb, logreg, mlp, markov1/2) found the best state-only model — mlp — at 48% raw next-tool accuracy vs 43% for a repeat-last baseline, and a learning curve showing the gain saturates fast: tool choice is mostly *sequential*, and the missing signal is tool-output content, which deidentified state can't carry. So: CPU routing works where the workflow shape is known (this suite: ~85% of calls, 0-call replays), and open-ended planning still needs the model — which is why the next experiment is reading tool decisions from model hidden states rather than observable state. Full table + the label-leakage bug this experiment caught: [`docs/benchmarks/trace-bakeoff.json`](docs/benchmarks/trace-bakeoff.json), [`benchmarks/README.md`](benchmarks/README.md).

### Capability tier — held-out tasks, three arms

Six harder tasks ([`benchmarks/tasks/`](benchmarks/tasks/)) graded against **held-out** pytest suites the agent never sees (`grade_patch` replays the agent's writes into a pristine repo and injects the hidden tests only there). 5 repeats × 6 tasks = 30 episodes per arm, `temperature=0.7`, memory fresh. **baseline** sends every decision to the LLM; **context** is the escalate-only control (CPU policy handles nothing, every decision is an LLM call); **hive** routes mechanical transitions through the CPU policy.

| Arm | Resolve rate | 95% CI | pass^5 | USD/resolved |
|---|---|---|---|---|
| baseline | **73%** (22/30) | [56%, 86%] | **50%** pass^5 | $0.0180/resolved |
| context | **87%** (26/30) | [70%, 95%] | **67%** pass^5 | —/resolved |
| hive | **47%** (14/30) | [30%, 64%] | **33%** pass^5 | $0.0097/resolved |

Verdicts (exact McNemar over per-task majority outcomes, n=6 tasks): baseline vs context: **not_separable** (p=1.0); baseline vs hive: **not_separable** (p=1.0); context vs hive: **not_separable** (p=0.5). At this suite size no pair separates — the honest read is that the held-out tier discriminates *within* an arm (per-task spread: hive drops `interval-merge-tiebreak`, `cache-key-collision`, `retry-budget-shared` entirely) but cannot yet rank arms. The suite was hardened after a first calibration showed saturation (20/20 at temp 0.3); the published numbers are the post-hardening run.

**Provenance:** baseline outcomes are the original run's; its token usage was re-measured on an identical 30-episode pass (that pass independently resolved 26/30 — a second draw, recorded in the artifact). Context's USD is `—` because 15/30 episodes lost their token record when the run was interrupted; the field is null, not zero. Raw artifact: [`docs/benchmarks/hive-bench-capability.json`](docs/benchmarks/hive-bench-capability.json).

*Note: an earlier revision of this README cited a "20-instance SWE-bench-lite" table (85% vs 0%). That harness simulated the agent loop and drew resolve outcomes from an RNG — the numbers were not real, and the script (`scripts/hive_swebench_eval.py`) has been replaced with a deprecation shim forwarding to `hive_bench.py`.*

### Compression behavior

`rule_fast` keeps small file reads verbatim (`CORE`) and compacts large ones to a code skeleton; test output is distilled to the pass/fail summary plus the failing asserts. On this suite the context payload appended to the transcript is ~1.2× smaller than raw observations — modest here because source files are (correctly) kept whole. Compression pays off on long tool output and logs; run `python scripts/hive_long_context_eval.py --smoke` for the long-context evidence.

---

## What Hive does

Three jobs, all on the CPU, before the LLM is involved:

1. **Routes mechanical decisions** — `read_file`, `run_tests`, `apply_patch`, etc. go to a trained CPU policy (busyBee-cpu). No LLM call. Out-of-distribution states escalate to the LLM instead of guessing.
2. **Compresses context** — a content-type-aware classifier labels each message and drops or distills the wax (stale logs, unchanged files) so the LLM sees only the honey.
3. **Remembers causally** — a timestamped graph store (rust-brain) records cause → effect → supersession chains, so the agent can later answer "why did this happen / what fixed it". A **logical clock** keeps that history in the right order: it tracks sequence and cause-and-effect between events, not just the time of day, so memory stays correct even when messages arrive late or out of sequence.

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
├── backend.py          # HIVE_BACKEND resolution (python default; native opt-in)
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
├── policy_updater.py   # PolicyUpdater + LinUCB contextual bandit; retrains busyBee from feedback
├── ab_test.py          # ABTestHarness — guarded A/B of policy updates
├── llm.py              # LLM client (sync urllib + async httpx); OpenAI-compatible + echo
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
├── gossip.py           # GossipProtocol — cross-node replication preserving causal
│                       #   edges + HLC, optional shared-token auth
├── deployment.py       # DeploymentMarker — blue-green / canary rollout markers
│
│   observability
├── telemetry.py        # Telemetry collector (routing/compression/memory events)
├── tracing.py          # W3C traceparent distributed tracing (TraceContext, Span)
├── hardware.py         # NVML power/util sampling (PowerSampler)
│
│   extensibility
├── plugins.py          # register custom compressors / routers (opt-in; not auto-wired)
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

Full stack (trained CPU router + ML compressor — siblings via git until published on PyPI):

```bash
pip install "hive-agent-memory[full]"
```

Optional extras:

```bash
pip install "hive-agent-memory[observability]"   # Prometheus + OpenTelemetry
pip install "hive-agent-memory[monitor]"         # NVML hardware monitoring
pip install "hive-agent-memory[performance]"     # native Rust backend (hive-cpp)
pip install "hive-agent-memory[gpu]"             # torch + transformers (examples / integration)
pip install "hive-agent-memory[agents]"          # FastAPI server + MCP server (stdio/SSE)
pip install "hive-agent-memory[http]"            # httpx async LLM client
pip install "hive-agent-memory[server]"          # FastAPI + uvicorn only
pip install "hive-agent-memory[mcp]"             # MCP server only
```

From source (development — reproducible lockfile + siblings for full-stack work):

```bash
git clone https://github.com/DJLougen/hive.git
cd hive
pip install -e ".[dev]"          # or: uv sync
pre-commit install
pytest
```

For publishing and one-time PyPI setup, see [`docs/PYPI.md`](docs/PYPI.md).

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
    validate=False,           # Pydantic validation: normalizes route() state and
                              #   rejects invalid remember() writes
    config=None,              # HiveConfig
    rate_limiter=None,        # RateLimiter (per-tenant)
    circuit_breaker=None,     # CircuitBreaker for the LLM path
    gossip=None,              # GossipProtocol — remember() publishes each node
                              #   to peers when attached
    max_content_bytes=1_048_576,
    backend=None,             # "python" (default) | "native" | "auto"; or set HIVE_BACKEND
)
```

With `config=HiveConfig(audit_enabled=True)` the stack also keeps a bounded
(10k) in-memory audit trail of `route` / `remember` / `record_outcome` calls —
including rejected feedback, which is the policy-poisoning signal. Read it with
`stack.audit_events()` and ship it to your SIEM via
`hive.audit_export.AuditExporter`.

Methods: `route`, `compress`, `compress_many`, `remember`, `recall`, `record_outcome`, `should_update_policy`, `update_policy`, `step`, `stats`. The causal store is exposed directly as `stack.brain`. `stats()` includes the active `backend` (`python` or `native`).

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
    stack.update_policy()            # retrains busyBee (or LinUCB policy) in place; returns bool
```

`record_outcome` matches feedback against a bounded window of recent `route()` calls (32 decisions) — out-of-order outcomes still land on the state that produced them, and feedback for an unknown decision is rejected as a possible policy-poisoning attempt. For contextual-bandit routing without sklearn, pass a `LinUCBPolicy` from `hive.policy_updater` as `busybee_policy`.

---

## Components

**busyBee-cpu** — CPU action routing
- Best state-only model in the trace bake-off: **48.0%** raw next-tool accuracy vs 43.1% repeat-last and 37.9% majority ([`docs/benchmarks/trace-bakeoff.json`](docs/benchmarks/trace-bakeoff.json)); in-distribution workflow shapes route far better — see the boundary discussion above.
- **OOD performance is unproven**; out-of-distribution states escalate to the LLM rather than guess.

**honey-comb / rule_fast** — context compression
- 6-label scheme — `CORE, DISTILL, COMPACT, DROP, STALE, ESCALATE` — driven by an 11-value `ContentType` taxonomy (`TOOL_RESULT_TEST`, `AGENT_PATCH`, `TOOL_RESULT_FILE`, …).
- `rule_fast` is the dependency-free in-repo fast path; `honey-comb` is the full compressor.
- Compression ratio scales with input length: ~1.0× on short agent turns, high ratios on long tool output / logs.

**rust-brain** — causal memory
- A **logical clock** (Hybrid Logical Clock) tracks event order and cause-and-effect, not just wall-clock time — memory stays correctly ordered when messages arrive late or out of sequence.
- Timestamped graph with monotonic ordering (`TimestampRegression` on stale writes).
- Edge kinds: `related_to`, `caused_by`, `supersedes`, `attached_to`.
- Per-tenant isolation, TTL + LRU eviction, optional vector search (`semantic_search.SemanticIndex`). Data is durable: `snapshot_to_file()` (gzip+SHA256) and `restore_from_file()` persist memory across restarts.
- ~177K writes/s (`docs/benchmarks/latest-micro.json`).

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
# → ["endpoint_health"]: the superseded link on the live node.
history = stack.brain.history("endpoint_health")
# → [MemoryNode(...)]: the original 500 / pool-exhausted observation itself,
#   retained as a bounded supersession chain (also persisted in snapshots).
#   The agent reconstructs "this was fixed two weeks ago by raising the pool" —
#   something a pure vector store cannot recover from embeddings alone.
```

---

## Performance

Component micro-benchmarks, measured on one host (synthetic load; raw data in [`docs/benchmarks/latest-micro.json`](docs/benchmarks/latest-micro.json)):

| Component | Items/s (measured host) |
|---|---|
| rust_brain | 176,796 |
| compress[fast] | 19,914 |
| compress[honeycomb] | 1,185 |
| busybee_cpu | 112 |

`busybee_cpu` measures 111.7 items/s in `latest-micro.json`. `latest-macro.json` reports 1,734,892/s for
the same component **with `policy_loaded: false`** — a constant-escalate fallback, not routing — so that
figure is not published until it is re-measured with a policy loaded.

Reproduce:

```bash
python scripts/hive_benchmark.py          # macro (full stack)
python scripts/hive_benchmark_micro.py    # micro (per component)
```

Energy methodology and raw NVML samples live in [`docs/energy.md`](docs/energy.md).

---

## Native Rust backend (hive-cpp)

`hive-cpp` is an optional Rust implementation of the hot paths (router, compressor, memory store). Control it with `HIVE_BACKEND` or the `backend=` constructor argument:

```bash
export HIVE_BACKEND=native    # REQUIRED to use hive-cpp for route/compress
export HIVE_BACKEND=python    # Python reference implementation
export HIVE_BACKEND=auto      # default: stays on python, so installing the crate changes nothing
```

Native is opt-in on purpose. The crate's compressor is lossy (it keeps `ceil(n/2)` whitespace tokens by an
importance score and rejoins them), so a backend that flipped to it merely because a wheel was importable
would silently change what the model sees. `auto` therefore never selects native by itself.

```bash
pip install "hive-agent-memory[performance]"   # or: pip install hive-cpp
```

Wheels for Linux / macOS / Windows (x86_64 + aarch64) are built by the `rust-wheels` CI job on tagged releases. See [`hive-cpp/README.md`](hive-cpp/README.md) for component benchmarks and build-from-source (maturin).

*Note: Memory still uses the Python `RustBrain` reference store today; native route/compress paths are wired. PFN-based busyBee training mode is landing separately.*

---

## Deployment

Hive ships container and orchestration assets:

- **HTTP server** — [`scripts/hive_api_server.py`](scripts/hive_api_server.py) (FastAPI). Endpoints: `POST /route`, `POST /compress`, `POST /remember`, `GET /recall`, plus `GET /health` and `GET /ready` probes. Set `HIVE_API_TOKEN` to require `Authorization: Bearer <token>` on the data endpoints (probes and OpenAPI stay public; unset = open for local dev). `AsyncHiveStack` backs high-throughput deployments.
- **MCP server** — [`hive-mcp`](hive/mcp_server.py) (stdio or SSE). Install with `pip install "hive-agent-memory[agents]"`. Bundled configs for **Cursor**, **Claude Desktop**, and **Codex** — see [docs/MCP_SETUP.md](docs/MCP_SETUP.md). Quick install: `python -m hive.mcp install --all`.
- **Harness integration** — Hermes, OpenClaw, SWE-bench eval, and MCP bridge — see [docs/HARNESS_SETUP.md](docs/HARNESS_SETUP.md).
- **Helm chart** — [`deploy/helm/`](deploy/helm/) (chart `0.6.1`).
- **Raw K8s manifests** — [`deploy/k8s/`](deploy/k8s/) (Deployment, Service, ConfigMap).
- **ARM64 image** — [`docker/Dockerfile.aarch64`](docker/Dockerfile.aarch64) for Jetson / Grace.

Enterprise concerns are first-class modules, documented in [docs/USAGE.md](docs/USAGE.md):

| Concern | Module |
|---|---|
| AuthN / AuthZ | `hive.auth` — JWT + RBAC |
| Encryption at rest | `hive.encryption` |
| Untrusted-model safety | `hive.model_registry` — Ed25519-signed `.joblib` (`sign_model()`), blocks pickle RCE |
| Audit / SIEM | `HiveConfig(audit_enabled=True)` + `hive.audit_export` |
| Rate limiting | `hive.ratelimit` — per-tenant token bucket |
| Circuit breaking | `hive.circuitbreaker` |
| Multi-tenancy | `RustBrain(tenant_id=…, tenant_isolation=True)` — supports `revoke_tenant()` for GDPR Article 17 mass-erase |
| Observability | `hive.telemetry`, `hive.tracing` (W3C traceparent), Prometheus / OTel |
| Health probes | `hive.health` |
| Audit | `hive.audit_export` (SIEM-compatible) |

---

## Development

```bash
pip install -e ".[dev]"    # includes ruff, mypy, pip-audit, pre-commit
# or: uv sync              # uses uv.lock for reproducible dev deps

pre-commit install

# Run the suite (full suite)
pytest

# Focused runs
pytest tests/test_stack.py -v
pytest tests/test_rust_brain.py tests/test_rust_brain_concurrency.py tests/test_hlc_snapshot_gossip.py -v
pytest tests/test_online_learning.py tests/test_linucb_policy.py -v
pytest tests/test_enterprise_auth.py tests/test_security_fixes.py -v

# Lint & types (matches CI)
ruff check hive/ tests/
mypy hive/ --ignore-missing-imports
pip-audit --skip-editable
```

CI (`.github/workflows/ci.yml`) runs the suite on **Python 3.10–3.13**, benchmark + long-context smoke tests, ruff, mypy, bandit, pip-audit, modular pentest, MCP smoke, SBOM generation on `main`, and nightly GPU/Jetson smoke (when self-hosted runners are registered).

---

## Roadmap

- [x] Core orchestration (routing, compression, causal memory)
- [x] Enterprise modules (auth, encryption, audit, rate limiting, multi-tenancy)
- [x] Observability (telemetry, W3C tracing, Prometheus / OpenTelemetry)
- [x] Online learning + guarded policy A/B
- [x] Native Rust backend (hive-cpp) with multi-platform wheels
- [x] Real-workload SWE-bench-lite A/B evaluation
- [x] MCP + FastAPI agent extras (`[agents]`, `[server]`, `[mcp]`)
- [x] `HIVE_BACKEND` native/python switching, LinUCB online learning, httpx async LLM
- [x] HLC-preserving snapshot restore + gossip replay; `uv.lock` + Dependabot
- [ ] PFN-based busyBee training mode (inference + campaign retrain from `FeedbackBuffer`)
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
