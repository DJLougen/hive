# Hive Enterprise Compliance Checklist

**Version**: 0.4.0  
**Date**: 2026-06-02  
**Status**: Partial — see gaps below

---

## SOC 2 Type II

| Control | Status | Evidence Location |
|---------|--------|-------------------|
| CC6.1 — Logical access controls | ✅ | Tenant isolation in `hive/rust_brain/__init__.py` |
| CC6.2 — Prior to access | ⚠️ | JWT auth stub exists; needs production IdP integration |
| CC6.3 — Access removal | ❌ | No automated offboarding workflow |
| CC6.6 — Encryption at rest | ⚠️ | RustBrain stores plaintext; encryption layer planned v0.5 |
| CC6.7 — Encryption in transit | ✅ | HTTPS enforced in `hive/llm.py` via URL validation |
| CC7.2 — System monitoring | ✅ | Prometheus + OpenTelemetry in `hive/telemetry.py` |
| CC8.1 — Change management | ✅ | GitHub PRs, CI gates, CHANGELOG.md |

## GDPR

| Requirement | Status | Notes |
|-------------|--------|-------|
| Article 17 — Right to erasure | ✅ | `RustBrain.forget()` + `gc_expired()` |
| Article 25 — Data protection by design | ⚠️ | TTL support exists but not default-enabled |
| Article 32 — Security of processing | ⚠️ | Encryption at rest planned v0.5 |
| Records of processing | ✅ | Audit log (`hive/audit.py`) with hash chain |

## ISO 27001

| Annex A Control | Status | Evidence |
|-----------------|--------|----------|
| A.9.1 — Access control policy | ⚠️ | Tenant isolation + rate limiting; needs policy doc |
| A.12.3 — Information backup | ❌ | No automated backup strategy |
| A.12.4 — Logging | ✅ | JSONL + Prometheus + OTel |
| A.14.2 — Secure development | ✅ | CI, lint, type check, security scan (bandit) |

---

## Gaps Requiring Action

1. **Encryption at rest** — Planned for v0.5.0
2. **Backup / disaster recovery** — Not implemented
3. **Production IdP integration** — JWT auth is a stub; needs Keycloak/Okta
4. **Penetration test report** — Not yet conducted
5. **Incident response runbook** — Not yet written
