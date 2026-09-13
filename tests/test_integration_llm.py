"""Integration tests against real inference backends (vLLM, llama.cpp).

These tests are skipped when the backend is not running locally.
To run them::

    vllm serve TinyLlama/TinyLlama-1.1B-Chat-v1.0 --port 8000
    pytest tests/test_integration_llm.py -v --run-integration
"""

from __future__ import annotations

import sys

import pytest

from hive.circuitbreaker import CircuitBreaker
from hive.llm import discover_local_endpoints, make_backend, probe_endpoint

RUN_INTEGRATION = "--run-integration" in sys.argv


def test_discover_local_endpoints():
    # Should not crash even if nothing is running
    endpoints = discover_local_endpoints()
    assert isinstance(endpoints, list)


def test_probe_endpoint_unreachable():
    with pytest.raises(RuntimeError, match="unreachable"):
        probe_endpoint("http://127.0.0.1:9999", timeout=0.1)


@pytest.mark.skipif(
    not RUN_INTEGRATION,
    reason="Pass --run-integration to test against real vLLM/llama.cpp",
)
def test_vllm_chat_smoke():
    try:
        probe_endpoint("http://127.0.0.1:8000", timeout=0.5)
    except RuntimeError:
        pytest.skip("vLLM not running on port 8000")

    backend = make_backend("vllm", endpoint="http://127.0.0.1:8000")
    response = backend.chat(
        [{"role": "user", "content": "Say hello"}],
        max_tokens=10,
    )
    assert response.text
    assert response.completion_tokens > 0
    assert response.duration_s > 0


@pytest.mark.skipif(
    not RUN_INTEGRATION,
    reason="Pass --run-integration to test against real vLLM/llama.cpp",
)
def test_llama_cpp_chat_smoke():
    try:
        probe_endpoint("http://127.0.0.1:8080", timeout=0.5)
    except RuntimeError:
        pytest.skip("llama.cpp not running on port 8080")

    backend = make_backend("llama.cpp", endpoint="http://127.0.0.1:8080")
    response = backend.chat(
        [{"role": "user", "content": "Say hello"}],
        max_tokens=10,
    )
    assert response.text
    assert response.completion_tokens > 0


def test_echo_backend_deterministic():
    backend = make_backend("echo")
    r1 = backend.chat([{"role": "user", "content": "test"}])
    r2 = backend.chat([{"role": "user", "content": "test"}])
    assert r1.text == r2.text


def test_circuit_breaker_with_llm_backend():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=1.0)
    # Only OpenAI-compat backends accept circuit_breaker
    backend = make_backend("vllm", endpoint="http://127.0.0.1:8000", circuit_breaker=cb)
    assert backend.circuit_breaker is cb
