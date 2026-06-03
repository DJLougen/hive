"""Tests for enterprise health and readiness probes."""

from __future__ import annotations

import time
import urllib.request

import pytest

from hive import HiveStack
from hive.health import HealthServer, is_healthy
from hive.rule_fast import RuleFastHoneyComb


def test_is_healthy_returns_ready_with_stack():
    stack = HiveStack(honey_comb=RuleFastHoneyComb())
    ready, backends = is_healthy(stack)
    assert isinstance(ready, bool)
    assert "rust_brain" in backends
    assert "compressor" in backends
    assert "policy" in backends


def test_health_server_returns_200():
    stack = HiveStack(honey_comb=RuleFastHoneyComb())
    with HealthServer(stack, port=18080, bind_address="127.0.0.1"):
        time.sleep(0.3)
        req = urllib.request.Request("http://127.0.0.1:18080/health")
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            assert resp.status == 200
            body = resp.read().decode("utf-8")
            assert '"status": "healthy"' in body
            assert '"uptime_s"' in body


def test_ready_endpoint_with_all_backends():
    stack = HiveStack(honey_comb=RuleFastHoneyComb())
    with HealthServer(stack, port=18081, bind_address="127.0.0.1"):
        time.sleep(0.3)
        req = urllib.request.Request("http://127.0.0.1:18081/ready")
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            assert resp.status == 200
            body = resp.read().decode("utf-8")
            assert "rust_brain" in body
            assert "ok" in body


def test_ready_endpoint_returns_503_without_compressor():
    stack = HiveStack(honey_comb=RuleFastHoneyComb())
    # Manually break compressor to simulate failure
    stack.comb = None
    with HealthServer(stack, port=18082, bind_address="127.0.0.1"):
        time.sleep(0.3)
        req = urllib.request.Request("http://127.0.0.1:18082/ready")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=2.0)
        assert exc_info.value.code == 503
