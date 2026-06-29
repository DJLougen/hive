# Hive Usage Guide

**Version**: 0.6.1  
**Target audience**: Engineers deploying Hive in production

---

## Table of Contents

1. [Installation](#installation)
2. [Quick Start](#quick-start)
3. [Core API](#core-api)
4. [Enterprise Features](#enterprise-features)
   - [Multi-tenancy](#multi-tenancy)
   - [Schema Validation](#schema-validation)
   - [Rate Limiting](#rate-limiting)
   - [Config Management](#config-management)
   - [Encryption at Rest](#encryption-at-rest)
   - [JWT Authentication](#jwt-authentication)
   - [Data Retention / TTL](#data-retention--ttl)
   - [Health Probes](#health-probes)
   - [Backup & Restore](#backup--restore)
5. [Observability](#observability)
6. [Security](#security)
7. [Deployment](#deployment)
8. [Testing](#testing)

---

## Installation

### Basic

```bash
pip install hive-agent-memory
```

### Full stack (CPU router + ML compressor)

```bash
pip install "hive-agent-memory[full]"
```

### With all extras

```bash
pip install "hive-agent-memory[dev,monitor,observability]"
```

| Extra | What it adds |
|-------|-------------|
| `full` | busybee-cpu + honey-comb (from PyPI) |
| `dev` | pytest, ruff, mypy |
| `monitor` | pynvml (GPU monitoring) |
| `observability` | Prometheus, OpenTelemetry |
| `gpu` | torch, transformers |

### Native Rust backend (optional)

```bash
pip install hive-cpp  # PyO3 wheel, ~100x faster memory ops
```

---

## Quick Start

```python
from hive import HiveStack

stack = HiveStack()

# 1. Route a decision locally (no LLM call for mechanical choices)
state = {"goal": "Fix auth bug", "step": 1, "available_tools": ["read_file", "run_tests"]}
decision = stack.route(state)
print(decision.tool)      # "read_file" or "escalate" if uncertain

# 2. Compress a bloated message before sending to LLM
compressed = stack.compress("user", "5000 lines of test output...")
print(compressed.label)   # "distill" — LLM only sees the summary

# 3. Remember causal state
stack.remember("auth_fix_attempt", {"file": "auth.py", "change": "added null check"})

# 4. Recall with graph traversal
value = stack.recall("auth_fix_attempt")
```

---

## Core API

### `HiveStack`

The orchestrator. Thin layer that wires busyBee-cpu, honey-comb, and rust-brain.

```python
from hive import HiveStack
from hive.rust_brain import RustBrain
from hive.ratelimit import RateLimiter
from hive.config import HiveConfig

stack = HiveStack(
    tenant_id="org_a",           # isolate memory per tenant
    validate=True,                # enforce Pydantic schemas
    config=HiveConfig(
        rate_limit=100,
        tenant_isolation=True,
        default_ttl_s=86400,      # 24h memory retention
    ),
    rate_limiter=RateLimiter(
        default_capacity=50,
        refill_rate=10.0,         # 10 tokens/second
    ),
)
```

### `route(state)` → `RouteDecision`

```python
decision = stack.route({
    "goal": "Debug failing test",
    "available_tools": ["read_file", "run_tests", "apply_patch"],
})

# decision.tool      → "run_tests"
# decision.args      → {}
# decision.confidence → 0.92
# decision.source    → "busybee" or "fallback"
# decision.escalated → False
```

### `compress(role, content)` → `CompressedTurn`

```python
turn = stack.compress("user", "Here are 5000 lines of logs...")

# turn.role   → "user"
# turn.content → "Here are 5000 lines of logs..." (possibly rewritten)
# turn.label  → "distill" (CORE / DISTILL / COMPACT / DROP / STALE / ESCALATE)
```

### `remember(key, value)` / `recall(key)`

```python
stack.remember("endpoint", "/v1/users", trust=1.0, tags={"api", "v1"})

# With edges (causal memory)
stack.remember("fix_auth", "added null check", edges={"caused_by": ["auth_bug"]})

# Recall
value = stack.recall("endpoint")        # → "/v1/users"
missing = stack.recall("nope")         # → None
```

### `step(state, transcript)` — full turn

```python
result = stack.step(
    state={"goal": "Fix auth", "step": 1},
    transcript=[
        ("user", "Login is broken"),
        ("assistant", "Let me check..."),
    ],
)
# Returns {decision, compressed, memory} in one call
```

---

## Enterprise Features

### Multi-tenancy

```python
from hive.rust_brain import RustBrain

# Each tenant's keys are prefixed internally: "tenant_id:key"
brain_a = RustBrain(tenant_id="org_a", tenant_isolation=True)
brain_b = RustBrain(tenant_id="org_b", tenant_isolation=True)

brain_a.remember("secret", "data_a")
brain_b.remember("secret", "data_b")

assert brain_a.recall("secret") == "data_a"
assert brain_b.recall("secret") == "data_b"
# Cross-tenant reads return None
```

### Schema Validation

```python
from hive.schemas import validate_state

# Enabled on the stack
stack = HiveStack(validate=True)

# Validates on route():
# - goal: str, max 4096 chars
# - step: int >= 0
# - available_tools: list[str]
# Raises ValidationError on bad input
```

### Rate Limiting

```python
from hive.ratelimit import RateLimiter

limiter = RateLimiter(default_capacity=100, refill_rate=10.0)

stack = HiveStack(
    tenant_id="org_a",
    rate_limiter=limiter,
)

# When bucket is empty, route() returns:
# RouteDecision(tool="escalate", source="ratelimit", confidence=0.0)
```

### Config Management

Environment variables:

| Variable | Default | Meaning |
|----------|---------|---------|
| `HIVE_RATE_LIMIT` | 0 | Requests/second cap |
| `HIVE_TENANT_ISOLATION` | true | Enable key prefixing |
| `HIVE_DEFAULT_TTL_S` | None | Memory TTL in seconds |
| `HIVE_VALIDATE_INPUTS` | false | Pydantic validation |
| `HIVE_JWKS_URL` | None | JWT key server |
| `HIVE_JWT_ISSUER` | None | Token issuer |
| `HIVE_JWT_AUDIENCE` | None | Token audience |
| `HIVE_ENCRYPTION_KEY` | None | AES-256 passphrase |

```python
from hive.config import HiveConfig

cfg = HiveConfig.from_env()  # reads all HIVE_* variables
cfg.validate()               # raises ValueError on bad config

stack = HiveStack(config=cfg)
```

### Encryption at Rest

```python
import os
os.environ["HIVE_ENCRYPTION_KEY"] = "my-production-passphrase"

from hive.encryption import Encryptor

e = Encryptor.from_env()
ct = e.encrypt({"api_key": "sk-123"})
pt = e.decrypt(ct)  # → {"api_key": "sk-123"}
```

No env var → encryption is **disabled** (backward compatible).

### JWT Authentication

```python
from hive.auth import JWTValidator, AuthError

# From JWKS endpoint
validator = JWTValidator.from_jwks("https://idp.example.com/.well-known/jwks.json")

# From environment
validator = JWTValidator.from_env()

try:
    claims = validator.validate(token, required_roles=["hive:admin"])
except AuthError as e:
    # Token expired, invalid signature, missing role
    raise HTTPException(status_code=401, detail=str(e))
```

### Data Retention / TTL

```python
from hive.rust_brain import RustBrain

brain = RustBrain(default_ttl_s=86400)  # 24h TTL
brain.remember("temp", "data")

# Check if expired
if brain.expire("temp"):
    print("Removed stale entry")

# Bulk GC
removed = brain.gc_expired()
print(f"Cleaned up {removed} expired entries")
```

### Health Probes

```python
from hive.health import HealthServer

server = HealthServer(stack, port=8080)
server.start()  # blocking; run in thread

# Endpoints:
# GET /health → 200 (liveness)
# GET /ready  → 200 if all backends OK, 503 if degraded
```

Kubernetes:

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 8080
readinessProbe:
  httpGet:
    path: /ready
    port: 8080
```

### Backup & Restore

```python
# Snapshot
meta = brain.snapshot_to_file("/backup/hive-2024-01-01.gz")
print(meta["sha256"])  # integrity checksum

# Restore
brain.restore_from_file("/backup/hive-2024-01-01.gz")
```

---

## Observability

### Telemetry

```python
from hive.telemetry import Telemetry

tel = Telemetry()
stack = HiveStack(telemetry=tel)

# After operations
print(tel.summary())
```

### Prometheus

```python
tel.start_prometheus_server(port=9090)
# Metrics on http://127.0.0.1:9090/metrics (localhost only)
# hive_routing_total{source,action}
# hive_routing_latency_ms_bucket
# hive_memory_reads_total{hit}
```

### OpenTelemetry

```python
tel.enable_otel_traces()
# Spans created for every route/compress/remember/recall
```

### JSONL Export

```python
# Real-time append
tel.enable_jsonl_append("/var/log/hive/events.jsonl")

# Batch flush
tel.export_jsonl("/var/log/hive/dump.jsonl")
```

---

## Security

Run the modular pentest before production:

```bash
python scripts/hive_pentest.py --module hive
```

With siblings installed:

```bash
python scripts/hive_pentest.py --active --fail-on-skip
```

Key hardening checklist:

- [ ] Set `HIVE_ENCRYPTION_KEY` in production
- [ ] Configure `HIVE_JWKS_URL` + `HIVE_JWT_ISSUER`
- [ ] Set `BUSYBEE_LEARN_API_KEY` (if using bee-serve)
- [ ] Load `.joblib` models **only from trusted paths** (pickle RCE risk)
- [ ] Run Prometheus behind a reverse proxy (binds 127.0.0.1 by default)
- [ ] Set `default_ttl_s` to prevent unbounded memory growth
- [ ] Enable `validate=True` on public-facing stacks

---

## Deployment

### Blue-Green Rollout

```python
from hive.deployment import DeploymentMarker

marker = DeploymentMarker(version="0.5.1", color="green")
marker.set_traffic_weight(0.10)

# After 100 successful requests
if marker.is_ready_for_promotion():
    marker.set_traffic_weight(1.0)  # promote
```

### Load Testing

```bash
python scripts/hive_load_test.py --duration 60 --rps 1000 --output report.json
```

### Chaos Engineering

```bash
python scripts/hive_chaos.py --mode latency --magnitude 0.1 --duration 30
```

---

## Testing

```bash
# All tests
pytest -v

# Enterprise tests only
pytest tests/test_enterprise_*.py -v

# Security regression tests
pytest tests/test_security_fixes.py -v

# Pentest runner tests
pytest tests/test_pentest_runner.py -v

# Lint
ruff check hive/ tests/
mypy hive/ --ignore-missing-imports
bandit -r hive/ -ll
```

---

## Further Reading

- [Architecture](architecture.md) — Component diagrams and data flow
- [Compliance Checklist](compliance-checklist.md) — SOC 2 / GDPR / ISO 27001
- [Incident Response](incident-response-runbook.md) — SEV-1/2/3 playbooks
- [RLHF Roadmap](rlhf-roadmap.md) — Future learning strategies
- [ROI Analysis](roi.md) — Cost savings breakdown
