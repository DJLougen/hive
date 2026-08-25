# Enterprise Roadmap

**Last updated:** 2026-08-25

This document tracks what is **shipped** vs **planned** for enterprise-grade Hive deployments.

## Shipped (v0.6.x)

| Area | Status | Location |
|------|--------|----------|
| Core orchestrator | ✅ | `hive/stack.py`, `hive/async_stack.py` |
| Causal memory (HLC) | ✅ | `hive/rust_brain/` |
| Schema validation | ✅ | `hive/schemas.py` |
| Multi-tenancy | ✅ | `RustBrain(tenant_id=...)` |
| Auth / JWT | ✅ | `hive/auth.py` |
| Encryption at rest | ✅ | `hive/encryption.py` |
| Rate limiting | ✅ | `hive/ratelimit.py` |
| Circuit breaker | ✅ | `hive/circuitbreaker.py` |
| Health probes | ✅ | `hive/health.py` |
| Audit export | ✅ | `hive/audit_export.py` |
| Gossip replication | ✅ | `hive/gossip.py` |
| K8s manifests | ✅ | `deploy/k8s/` |
| Helm chart | ✅ | `deploy/helm/` |
| Observability | ✅ | `hive/telemetry.py`, Prometheus + OTel extras |
| MCP server | ✅ | `scripts/hive_mcp_server.py` |
| FastAPI server | ✅ | `scripts/hive_api_server.py` |
| Native backend (hive-cpp) | ✅ beta | `hive-cpp/`, `HIVE_BACKEND=native` |
| LinUCB online learning | ✅ | `hive/policy_updater.py` |
| SWE-bench eval harness | ✅ | `scripts/hive_swebench_eval.py` |
| PyPI publishing | ✅ | `.github/workflows/release.yml` |
| SBOM + pip-audit CI | ✅ | `scripts/generate_sbom.py`, CI `sbom` job |

## In progress / next

| Area | Target | Notes |
|------|--------|-------|
| hive-cpp as default backend | v0.7 | PyPI wheels on tag; drop-in via `HIVE_BACKEND` |
| 50-instance SWE-bench on 7B+ model | v0.7 | Harness ready; needs GPU runner + model |
| DPO / offline RL policy updates | v0.8+ | See `docs/rlhf-roadmap.md` |
| SOC2 evidence automation | v0.8+ | `docs/soc2-evidence.md` is manual today |
| 99.9% SLA load test at 1M req/day | v0.8+ | Needs dedicated perf environment |

## What "enterprise grade" still means

1. **Performance** — hive-cpp default, <1 ms compression at scale
2. **Reliability** — 99.9%+ uptime with HA failover tested
3. **Observability** — Grafana dashboards + alerting wired in customer envs
4. **Scale** — Load tested at 1M+ requests/day on Kubernetes
5. **Compliance** — Automated SOC2 evidence collection

## Gap summary

The Python meta-package is **production-ready for single-GPU / small-team deployments** (10–100 agents). The remaining work is **scale proof**, **native backend by default**, and **compliance automation** — not greenfield feature development.
