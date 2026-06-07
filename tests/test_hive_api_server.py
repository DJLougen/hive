"""Tests for the Hive REST API server readiness probe."""

from __future__ import annotations

import pytest


try:
    from fastapi.testclient import TestClient

    import scripts.hive_api_server as api_server

    _HAS_FASTAPI = api_server._HAS_FASTAPI
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False


@pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")
def test_ready_returns_503_when_brain_is_down():
    """Regression: is_healthy() returns a tuple; truthiness must not be used."""
    client = TestClient(api_server.app)
    stack = api_server.stack

    class BrokenBrain:
        def stats(self) -> dict[str, int]:
            raise RuntimeError("brain down")

    original_brain = stack.brain
    try:
        stack.brain = BrokenBrain()  # type: ignore[assignment]
        resp = client.get("/ready")
        assert resp.status_code == 503
        assert resp.json()["status"] == "not_ready"
    finally:
        stack.brain = original_brain


@pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")
def test_ready_returns_200_when_stack_is_healthy():
    client = TestClient(api_server.app)
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


@pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")
def test_api_server_applies_validate_inputs_from_env(monkeypatch):
    """K8s sets HIVE_VALIDATE_INPUTS=true; the REST server must honor it."""
    from hive import HiveStack
    from hive.config import HiveConfig
    from hive.rule_fast import RuleFastHoneyComb

    monkeypatch.setenv("HIVE_VALIDATE_INPUTS", "true")
    # Rebuild stack with fresh env (module-level singleton).
    api_server.stack = HiveStack(
        honey_comb=RuleFastHoneyComb(),
        config=HiveConfig.from_env(),
    )
    client = TestClient(api_server.app)
    resp = client.post("/route", json={"goal": "x", "step": -1, "available_tools": []})
    assert resp.status_code == 422
    monkeypatch.delenv("HIVE_VALIDATE_INPUTS", raising=False)
    api_server.stack = HiveStack(
        honey_comb=RuleFastHoneyComb(),
        config=HiveConfig.from_env(),
    )
