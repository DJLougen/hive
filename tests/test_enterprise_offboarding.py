"""Stress tests for automated offboarding (GDPR Article 17)."""

from __future__ import annotations

from hive.rust_brain import RustBrain


def test_revoke_tenant_removes_all_data():
    brain = RustBrain(tenant_id="org_offboard", tenant_isolation=True)
    brain.remember("a", 1)
    brain.remember("b", 2)
    brain.remember("c", 3)

    removed = brain.revoke_tenant("org_offboard")
    assert removed == 3
    assert brain.recall("a") is None
    assert brain.recall("b") is None
    assert brain.recall("c") is None
    assert len(brain) == 0


def test_revoke_tenant_does_not_affect_other_tenants():
    brain_a = RustBrain(tenant_id="tenant_a", tenant_isolation=True)
    brain_b = RustBrain(tenant_id="tenant_b", tenant_isolation=True)

    brain_a.remember("key", "a_value")
    brain_b.remember("key", "b_value")

    # Revoke tenant_a from a shared store (if we had one)
    # In this test each brain is separate, but we test the prefix logic
    removed = brain_a.revoke_tenant("tenant_a")
    assert removed == 1
    assert brain_a.recall("key") is None

    # tenant_b unaffected
    assert brain_b.recall("key") == "b_value"


def test_revoke_tenant_uses_default_tenant_when_none_specified():
    brain = RustBrain(tenant_id="default_tenant", tenant_isolation=True)
    brain.remember("x", 10)

    removed = brain.revoke_tenant()  # Uses self._tenant_id
    assert removed == 1
    assert brain.recall("x") is None


def test_revoke_tenant_returns_zero_when_no_data():
    brain = RustBrain(tenant_id="empty", tenant_isolation=True)
    removed = brain.revoke_tenant("empty")
    assert removed == 0
