# Hive Enterprise Compliance Checklist

**Version**: 0.6.1  
**Date**: 2026-06-29  
**Status**: Partial — enterprise gaps plugged in v0.5.0; v0.6.x adds real-workload eval + snapshot integrity. See remaining gaps below.

---

## SOC 2 Type II

| Control | Status | Evidence Location |
|---------|--------|-------------------|
| CC6.1 — Logical access controls | ✅ | Tenant isolation in `hive/rust_brain/__init__.py` |
| CC6.2 — Prior to access | ✅ |  JWT validation with JWKS support + RBAC |
| CC6.3 — Access removal | ✅ |  wipes all tenant data + audit log entry |
| CC6.6 — Encryption at rest | ✅ |  AES-256-GCM; transparent encrypt on write, decrypt on read |
| CC6.7 — Encryption in transit | ✅ | HTTPS enforced in `hive/llm.py` via URL validation |
| CC7.2 — System monitoring | ✅ | Prometheus + OpenTelemetry in `hive/telemetry.py` |
| CC8.1 — Change management | ✅ | GitHub PRs, CI gates, CHANGELOG.md |

## GDPR

| Requirement | Status | Notes |
|-------------|--------|-------|
| Article 17 — Right to erasure | ✅ | `RustBrain.forget()` + `gc_expired()` |
| Article 25 — Data protection by design | ⚠️ | TTL support exists but not default-enabled |
| Article 32 — Security of processing | ✅ | AES-256-GCM + HMAC via  |
| Records of processing | ✅ | Audit log (`hive/audit.py`) with hash chain |

## ISO 27001

| Annex A Control | Status | Evidence |
|-----------------|--------|----------|
| A.9.1 — Access control policy | ⚠️ | Tenant isolation + rate limiting; needs policy doc |
| A.12.3 — Information backup | ✅ |  +  with SHA-256 integrity checks |
| A.12.4 — Logging | ✅ | JSONL + Prometheus + OTel |
| A.14.2 — Secure development | ✅ | CI, lint, type check, security scan (bandit) |

---

## Gaps Requiring Action

1. ~~**Encryption at rest**~~ — Implemented in v0.5.0
2. ~~**Backup / disaster recovery**~~ — Implemented in v0.5.0
3. ~~**Production IdP integration**~~ — Implemented in v0.5.0 (JWKS + RBAC)
4. **Penetration test report** — Not yet conducted (engage third-party firm)
5. ~~**Incident response runbook**~~ — Written: 
