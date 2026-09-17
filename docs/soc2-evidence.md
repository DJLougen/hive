# SOC 2 Evidence Stubs

**Control Environment**: Hive Agent Memory v0.6.1  
**Trust Service Criteria**: Security, Availability

This document maps controls to artifacts that exist in this repository.
Controls whose evidence cells cannot be filled from committed code or tests
are marked **not evidenced** — do not read a ✅ here as audit-ready.

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
- Semantic versioning (currently v0.6.1)

**Audit trail**: Git history + in-memory audit events in `hive/stack.py`
(`audit_enabled`), exportable to SIEM formats via `hive/audit_export.py`.
There is no `hive/audit.py` and no signed-log mechanism — treat "signed
logs" as **not evidenced**.

---

## CC6.6 — Encryption at Rest

**Evidence**: `hive/encryption.py` provides AES-256-GCM transparent encryption
(key derived from `HIVE_ENCRYPTION_KEY`).

**Test**: `tests/test_enterprise_encryption.py`

---

## CC6.2 / CC6.3 — Authentication & Offboarding

**Evidence**: `hive/auth.py` JWT validation with JWKS + RBAC.
`RustBrain.forget()` / tenant revocation for GDPR Article 17.

**Test**: `tests/test_enterprise_auth.py`, `tests/test_enterprise_offboarding.py`

---

## Availability Monitoring

**Health probes**: `hive/health.py`
- `/health` — liveness (always 200 if process alive)
- `/ready` — readiness (200 only if RustBrain + compressor responsive)

**SLA targets** (aspirational — no measured uptime or latency artifact exists):
- Uptime: 99.9%
- P95 routing latency: < 1ms
- P95 memory read latency: < 0.01ms

---

*This document is a stub. For full SOC 2 Type II certification, engage a CPA firm.*
