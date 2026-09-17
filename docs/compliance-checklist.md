# Hive Enterprise Compliance Checklist

**Version**: 0.6.1  
**Date**: 2026-06-29  
**Status**: Partial — enterprise gaps plugged in v0.5.0; v0.6.x adds real-workload eval + snapshot integrity. See remaining gaps below.

A ✅ means the named artifact exists in this repository and a test exercises
it; it does not mean the control has been audited. Rows marked "not
evidenced" have no committed artifact backing the claim.

---

## SOC 2 Type II

| Control | Status | Evidence Location |
|---------|--------|-------------------|
| CC6.1 — Logical access controls | ✅ | Tenant isolation in `hive/rust_brain/__init__.py`; `tests/test_enterprise_tenancy.py` |
| CC6.2 — Prior to access | ✅ | `hive/auth.py` JWT validation with JWKS support + RBAC; `tests/test_enterprise_auth.py` |
| CC6.3 — Access removal | ✅ | `RustBrain.revoke_tenant()` wipes all tenant data; `tests/test_enterprise_offboarding.py` |
| CC6.6 — Encryption at rest | ✅ | `hive/encryption.py` AES-256-GCM; transparent encrypt on write, decrypt on read; `tests/test_enterprise_encryption.py` |
| CC6.7 — Encryption in transit | ⚠️ not evidenced | `hive/llm.py` `_validate_url` accepts both `http://` and `https://`; no TLS enforcement exists |
| CC7.2 — System monitoring | ✅ | Prometheus + OpenTelemetry in `hive/telemetry.py` |
| CC8.1 — Change management | ✅ | GitHub PRs, CI gates, CHANGELOG.md |

## GDPR

| Requirement | Status | Notes |
|-------------|--------|-------|
| Article 17 — Right to erasure | ✅ | `RustBrain.forget()` + `gc_expired()` |
| Article 25 — Data protection by design | ⚠️ | TTL support exists but not default-enabled |
| Article 32 — Security of processing | ✅ | AES-256-GCM via `hive/encryption.py` (PBKDF2-HMAC-SHA256 key derivation) |
| Records of processing | ⚠️ not evidenced | In-memory audit events in `hive/stack.py` (`audit_enabled`) + SIEM export via `hive/audit_export.py`; there is no `hive/audit.py` and no hash chain |

## ISO 27001

| Annex A Control | Status | Evidence |
|-----------------|--------|----------|
| A.9.1 — Access control policy | ⚠️ | Tenant isolation + rate limiting; needs policy doc |
| A.12.3 — Information backup | ✅ | `RustBrain.snapshot_to_file()` / `restore_from_file()` with SHA-256 integrity checks; `tests/test_enterprise_backup.py` |
| A.12.4 — Logging | ✅ | JSONL + Prometheus + OTel (`hive/telemetry.py`) |
| A.14.2 — Secure development | ✅ | CI, lint, type check, security scan (bandit) |

---

## Gaps Requiring Action

1. ~~**Encryption at rest**~~ — Implemented in v0.5.0
2. ~~**Backup / disaster recovery**~~ — Implemented in v0.5.0
3. ~~**Production IdP integration**~~ — Implemented in v0.5.0 (JWKS + RBAC)
4. **Penetration test report** — Not yet conducted (engage third-party firm)
5. ~~**Incident response runbook**~~ — Written: `docs/incident-response-runbook.md`
6. **Encryption in transit** — `hive/llm.py` permits `http://` endpoints; no TLS enforcement
7. **Audit log integrity** — audit events are an in-memory deque; no hash chain or signing
