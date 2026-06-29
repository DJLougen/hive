# Hive Incident Response Runbook

**Version**: 0.6.1  
**Owner**: Hive SRE  
**Last updated**: 2026-06-02

---

## Severity Levels

| Level | Criteria | Response Time | Escalation |
|-------|----------|---------------|------------|
| **SEV-1** | Data loss, security breach, complete outage | 15 min | Page on-call immediately |
| **SEV-2** | Degraded performance, partial tenant isolation failure | 1 hour | Slack #hive-incidents |
| **SEV-3** | Single-node failure, non-critical alert | 4 hours | Next business day |

---

## Common Scenarios

### SEV-1: Tenant Data Leakage

**Detection**: `test_enterprise_tenancy.py` fails, or `hive_routing_total{tenant="X"}` shows cross-tenant reads.

**Steps**:
1. Immediately freeze the affected `RustBrain` instance:
   ```python
   from hive.rust_brain import RustBrain
   brain.revoke_tenant("affected_tenant")
   ```
2. Verify no other tenants are impacted:
   ```python
   for t in all_tenant_ids:
       assert brain.recall(f"{t}:test_key", " sentinel") == " sentinel"
   ```
3. Take a snapshot for forensics:
   ```python
   brain.snapshot_to_file("/tmp/forensics-snapshot.gz")
   ```
4. Notify security team and affected tenant within 1 hour (GDPR Article 33).
5. Rotate encryption keys if `HIVE_ENCRYPTION_KEY` was in scope.

### SEV-1: Authentication Bypass

**Detection**: `JWTValidator` logs show missing-signature errors, or unauthorized admin actions.

**Steps**:
1. Revoke all active sessions at the IdP level.
2. Disable JWT validation in Hive (`HIVE_JWKS_URL=`) until IdP confirms fix.
3. Rotate `HIVE_JWT_PUBLIC_KEY` if asymmetric.
4. Audit `hive/audit.py` signed logs for unauthorized access scope.

### SEV-2: RustBrain Memory Explosion

**Detection**: `hive_memory_writes_total` grows unbounded; `gc_expired()` returns 0.

**Steps**:
1. Check `default_ttl_s` is set:
   ```python
   brain.gc_expired()
   ```
2. If no TTL configured, set one and run GC:
   ```python
   brain._default_ttl_s = 86400  # 24h
   removed = brain.gc_expired()
   ```
3. If still growing, take snapshot and restart with `max_memory_nodes` limit in `HiveConfig`.

### SEV-2: Rate Limiter False Positives

**Detection**: Legitimate traffic receiving `source="ratelimit"` escalations.

**Steps**:
1. Check bucket capacity:
   ```python
   limiter = stack.rate_limiter
   print(limiter.check("tenant", "route"))
   ```
2. Temporarily increase `default_capacity` or `refill_rate`.
3. If under DDoS, enable circuit breaker in `hive.config` (`circuit_breaker=True`).

### SEV-3: Backup Restoration Failure

**Detection**: `restore_from_file()` raises `ValueError` on version mismatch.

**Steps**:
1. Verify snapshot version matches current Hive version.
2. Check SHA-256 integrity:
   ```bash
   python -c "
   import hashlib, gzip, json
   with open('snapshot.gz', 'rb') as f: data = gzip.decompress(f.read())
   print(hashlib.sha256(data).hexdigest())
   "
   ```
3. If corrupted, fall back to previous day's snapshot.

---

## Contacts

| Role | Slack | Escalation |
|------|-------|------------|
| On-call SRE | @hive-oncall | PagerDuty rotation |
| Security | #security | security@hive.dev |
| Product | #hive-product | product@hive.dev |

---

## Post-Incident Checklist

- [ ] Root cause documented in incident tracker
- [ ] `CHANGELOG.md` updated if code change required
- [ ] Regression test added to `tests/test_enterprise_*.py`
- [ ] Compliance checklist updated if control gap found
- [ ] Retrospective scheduled within 1 week
