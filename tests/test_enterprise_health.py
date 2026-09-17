"""Tests for enterprise health and readiness probes."""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest

from hive import HiveStack
from hive.health import HealthServer, is_healthy
from hive.rule_fast import RuleFastHoneyComb


def _server_answers(url: str) -> bool:
    """True once the endpoint responds (any status, including 503)."""
    try:
        urllib.request.urlopen(urllib.request.Request(url), timeout=2.0).close()
    except urllib.error.HTTPError:
        return True  # the server answered, with an error status
    except OSError:
        return False
    return True


def _get(url: str) -> str:
    with urllib.request.urlopen(urllib.request.Request(url), timeout=2.0) as resp:
        return resp.read().decode("utf-8")


def test_is_healthy_returns_ready_with_stack():
    stack = HiveStack(honey_comb=RuleFastHoneyComb())
    ready, backends = is_healthy(stack)
    assert isinstance(ready, bool)
    assert "rust_brain" in backends
    assert "compressor" in backends
    assert "policy" in backends


def test_health_server_returns_200(free_port, wait_until):
    stack = HiveStack(honey_comb=RuleFastHoneyComb())
    url = f"http://127.0.0.1:{free_port}/health"
    with HealthServer(stack, port=free_port, bind_address="127.0.0.1"):
        wait_until(lambda: _server_answers(url))
        body = _get(url)
    assert '"status": "healthy"' in body
    assert '"uptime_s"' in body


def test_ready_endpoint_with_all_backends(free_port, wait_until):
    stack = HiveStack(honey_comb=RuleFastHoneyComb())
    url = f"http://127.0.0.1:{free_port}/ready"
    with HealthServer(stack, port=free_port, bind_address="127.0.0.1"):
        wait_until(lambda: _server_answers(url))
        body = _get(url)
    assert "rust_brain" in body
    assert "ok" in body


def test_ready_endpoint_returns_503_without_compressor(free_port, wait_until):
    stack = HiveStack(honey_comb=RuleFastHoneyComb())
    # Manually break compressor to simulate failure
    stack.comb = None
    url = f"http://127.0.0.1:{free_port}/ready"
    with HealthServer(stack, port=free_port, bind_address="127.0.0.1"):
        wait_until(lambda: _server_answers(url))
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(urllib.request.Request(url), timeout=2.0)
        assert exc_info.value.code == 503
