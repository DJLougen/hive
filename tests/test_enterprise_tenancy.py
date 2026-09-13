"""Tests for enterprise multi-tenancy isolation."""

from __future__ import annotations

from hive import HiveStack
from hive.rule_fast import RuleFastHoneyComb
from hive.rust_brain import RustBrain


def test_tenant_a_cannot_read_tenant_b():
    brain_a = RustBrain(tenant_id="tenant_a")
    brain_b = RustBrain(tenant_id="tenant_b")

    brain_a.remember("secret", "tenant_a_value")
    brain_b.remember("secret", "tenant_b_value")

    assert brain_a.recall("secret") == "tenant_a_value"
    assert brain_b.recall("secret") == "tenant_b_value"


def test_same_tenant_can_read():
    brain = RustBrain(tenant_id="shared")
    brain.remember("key", "value")
    assert brain.recall("key") == "value"


def test_default_tenant_backward_compat():
    brain = RustBrain()
    brain.remember("key", "default_value")
    assert brain.recall("key") == "default_value"
    assert "default" in repr(brain)


def test_tenant_isolation_via_stack():
    stack_a = HiveStack(honey_comb=RuleFastHoneyComb(), tenant_id="org_a")
    stack_b = HiveStack(honey_comb=RuleFastHoneyComb(), tenant_id="org_b")

    stack_a.remember("config", {"db": "org_a_db"})
    stack_b.remember("config", {"db": "org_b_db"})

    assert stack_a.recall("config") == {"db": "org_a_db"}
    assert stack_b.recall("config") == {"db": "org_b_db"}
