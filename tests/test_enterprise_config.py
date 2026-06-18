"""Tests for enterprise configuration management."""

from __future__ import annotations

import os

import pytest

from hive.config import HiveConfig
from hive import HiveStack
from hive.rule_fast import RuleFastHoneyComb


def test_from_env_reads_vars():
    os.environ["HIVE_RATE_LIMIT"] = "50"
    os.environ["HIVE_TENANT_ISOLATION"] = "false"
    os.environ["HIVE_JWT_SECRET"] = "test-secret"
    try:
        cfg = HiveConfig.from_env()
        assert cfg.rate_limit == 50
        assert cfg.tenant_isolation is False
        assert cfg.jwt_secret == "test-secret"
    finally:
        del os.environ["HIVE_RATE_LIMIT"]
        del os.environ["HIVE_TENANT_ISOLATION"]
        del os.environ["HIVE_JWT_SECRET"]


def test_from_env_uses_defaults():
    # Ensure no HIVE_ vars leak in
    for key in list(os.environ):
        if key.startswith("HIVE_"):
            del os.environ[key]
    cfg = HiveConfig.from_env()
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


def test_stack_applies_max_memory_nodes_from_config():
    cfg = HiveConfig(max_memory_nodes=3)
    stack = HiveStack(honey_comb=RuleFastHoneyComb(), config=cfg)
    assert stack.brain._max_nodes == 3
    for i in range(5):
        stack.remember(f"k{i}", i)
    assert len(stack.brain) == 3


def test_from_env_reads_hive_max_nodes_alias():
    os.environ["HIVE_MAX_NODES"] = "4"
    try:
        cfg = HiveConfig.from_env()
        assert cfg.max_memory_nodes == 4
    finally:
        del os.environ["HIVE_MAX_NODES"]


def test_config_to_dict():
    cfg = HiveConfig(rate_limit=100)
    d = cfg.to_dict()
    assert d["rate_limit"] == 100
    assert "validate_inputs" in d
