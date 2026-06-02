# SOC 2 Evidence Stubs

**Control Environment**: Hive Agent Memory v0.5.0  
**Trust Service Criteria**: Security, Availability

---

## CC6.1 — Logical Access Controls

**Evidence**: Tenant isolation in `hive/rust_brain/__init__.py`

```python
# Each tenant's keys are prefixed internally:
#   storage_key = f"{tenant_id}:{key}"
# Cross-tenant reads return None.
```

**Test**: `tests/test_enterprise_tenancy.py::test_tenant_a_cannot_read_tenant_b`

**Screenshot**: CI passing at <https://github.com/DJLougen/hive/actions>

---

## CC7.2 — System Monitoring

**Evidence**: `hive/telemetry.py` exports to Prometheus and OpenTelemetry.

**Metrics collected**:
- `hive_routing_total` — routing decisions per tenant
- `hive_routing_latency_ms` — p50/p95/p99 histograms
- `hive_memory_reads_total{hit="true"}` — cache hit rate

**Dashboard**: Grafana template available in `docs/grafana-dashboard.json` (stub).

---

## CC8.1 — Change Management

**Evidence**:
- All changes via GitHub PR
- CI runs: `ruff`, `mypy`, `bandit`, `pytest`
- CHANGELOG.md maintained
- Semantic versioning (currently v0.5.0)

**Audit trail**: Git history + `hive/audit.py` signed logs.

---

## CC6.6 — Encryption at Rest

**Evidence**:  provides AES-256-GCM transparent encryption.

**Test**: 

---

## CC6.2 / CC6.3 — Authentication & Offboarding

**Evidence**:  JWT validation with JWKS + RBAC.  for GDPR Article 17.

**Test**: , 

---

## Availability Monitoring

**Health probes**: `hive/health.py`
- `/health` — liveness (always 200 if process alive)
- `/ready` — readiness (200 only if RustBrain + compressor responsive)

**SLA targets** (aspirational):
- Uptime: 99.9%
- P95 routing latency: < 1ms
- P95 memory read latency: < 0.01ms

---

*This document is a stub. For full SOC 2 Type II certification, engage a CPA firm.*
