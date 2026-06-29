# Hive Migration Guide

**Version**: 0.6.0 → 0.6.1  
**Policy**: Semantic versioning with deprecation warnings.

---

## 0.6.0 → 0.6.1

No breaking API changes. Snapshot restore now verifies SHA-256 checksums when present.

```bash
pip install --upgrade "hive-agent-memory>=0.6.1,<0.7.0"
```

Pre-checksum snapshots (from 0.6.0 and earlier) restore without verification, as before.

---

## Deprecation Policy

Hive follows semantic versioning with a **2-release deprecation window**:

1. Feature is marked deprecated with a `DeprecationWarning` (release N)
2. Feature continues to work but warns (release N)
3. Feature is removed or behavior changes (release N+1)

---

## 0.4.x → 0.5.0 Breaking Changes

### `record_outcome()` now rejects forged decisions

**Before:**
```python
stack.record_outcome(forged_decision, "apply_patch", OutcomeType.CORRECT)
# → logged warning, but STILL enqueued the outcome
```

**After:**
```python
stack.record_outcome(forged_decision, "apply_patch", OutcomeType.CORRECT)
# → returns early, outcome NOT enqueued (prevents policy poisoning)
```

**Migration:** Ensure `record_outcome()` is called with the **same** `RouteDecision` returned by the most recent `route()` call. If you need to record outcomes from a different source, bypass `HiveStack` and use `FeedbackBuffer.add()` directly.

---

## 0.3.x → 0.4.x → 0.5.0 Deprecations

None. All changes were backward-compatible additions.

---

## Upcoming Deprecations (0.6.0+)

| Feature | Status | Replacement |
|---------|--------|-------------|
| `HiveStack.compress_many(turns)` | stable | no change |
| `RustBrain(max_nodes=None)` | deprecated | `RustBrain(max_nodes=10000)` (always bounded) |
| `Telemetry.start_prometheus_server(addr="0.0.0.0")` | deprecated | binds to `127.0.0.1` by default |

---

## Migration Checklist

- [ ] Update `record_outcome()` callers to pass the decision from `route()`
- [ ] Set `RustBrain(max_nodes=...)` explicitly if you relied on unbounded memory
- [ ] Verify Prometheus is behind a reverse proxy (binds `127.0.0.1` now)
- [ ] Enable `validate=True` if you want Pydantic schema enforcement
- [ ] Review `HIVE_*` environment variables (new in 0.5.0)

---

## Compatibility Matrix

| Hive Version | Python | PyJWT | pydantic | cryptography | Status |
|-------------|--------|-------|----------|--------------|--------|
| 0.3.x | 3.10+ | N/A | N/A | N/A | maintained |
| 0.4.x | 3.10+ | N/A | N/A | N/A | maintained |
| 0.5.x | 3.10+ | ≥2.8 | ≥2.0 | ≥41.0 | maintained |
| 0.6.x | 3.10+ | ≥2.10 | ≥2.10 | ≥43.0 | current |

---

## Enterprise Upgrade Path

```bash
# 1. Pin version
pip install "hive-agent-memory>=0.6.1,<0.7.0"

# 2. Run tests
pytest tests/ -v

# 3. Run security scan
python scripts/hive_pentest.py --module hive

# 4. Verify benchmarks
python scripts/hive_benchmark.py --transcript-turns 50 --brain-writes 500
```
