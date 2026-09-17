"""Tests for enterprise configuration management."""

from __future__ import annotations

import pytest

from hive import HiveStack
from hive.config import HiveConfig
from hive.rule_fast import RuleFastHoneyComb


def test_from_env_reads_vars(monkeypatch):
    monkeypatch.setenv("HIVE_RATE_LIMIT", "50")
    monkeypatch.setenv("HIVE_TENANT_ISOLATION", "false")
    monkeypatch.setenv("HIVE_JWT_SECRET", "test-secret")

    cfg = HiveConfig.from_env()

    assert cfg.rate_limit == 50
    assert cfg.tenant_isolation is False
    assert cfg.jwt_secret == "test-secret"


def test_from_env_uses_defaults():
    # A prefix no one sets yields pure defaults without touching the
    # environment (deleting every HIVE_* var would also remove CI's
    # HIVE_NO_NVML, and would leave the env mutated for the next test).
    cfg = HiveConfig.from_env(prefix="HIVE_TEST_UNSET_")
    assert cfg.rate_limit == 0
    assert cfg.tenant_isolation is True
    assert cfg.jwt_secret is None


def test_validate_required_fields():
    cfg = HiveConfig(rate_limit=-1)
    with pytest.raises(ValueError):
        cfg.validate()


def test_config_integration_with_stack():
    cfg = HiveConfig(validate_inputs=True)
    stack = HiveStack(honey_comb=RuleFastHoneyComb(), config=cfg)
    assert stack._validate is True


def test_config_to_dict():
    cfg = HiveConfig(rate_limit=100)
    d = cfg.to_dict()
    assert d["rate_limit"] == 100
    assert "validate_inputs" in d
