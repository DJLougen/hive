"""Tests for the hive-cpp native backend adapter (:mod:`hive.backend`).

The router/compressor/erasure bugs these tests pin down were invisible for two
releases because nothing executed the native path. The first four tests inject a
fake ``hive_cpp`` module so the adapter's argument marshalling is checked on any
machine; the last test runs against the real extension and is skipped unless
``HIVE_REQUIRE_NATIVE=1`` is set (the CI ``rust`` job sets it).
"""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import pytest

from hive.backend import native_compress, native_route

# Mirrors hive-cpp/src/router.rs::AgentState — serde requires every field.
AGENT_STATE_FIELDS = {
    "goal",
    "step",
    "last_tool",
    "recent_observations",
    "open_files",
    "available_tools",
}

MINIMAL_MODEL_JSON = json.dumps(
    {
        "root": {
            "feature": None,
            "threshold": None,
            "left": None,
            "right": None,
            "action": "read_file",
        },
        "feature_names": [],
        "tool_names": ["read_file"],
    }
)


def _require_native() -> bool:
    return os.environ.get("HIVE_REQUIRE_NATIVE") == "1"


def test_native_compress_maps_crate_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "compressed": "X",
        "original_tokens": 10,
        "compressed_tokens": 1,
        "ratio": 10.0,
        "latency_ms": 0.01,
    }
    monkeypatch.setitem(
        sys.modules,
        "hive_cpp",
        SimpleNamespace(rust_compress=lambda text: json.dumps(payload)),
    )

    out = native_compress("tool", "abc")

    assert out["compressed"] == "X"
    assert out["original_tokens"] == 10
    assert out["compressed_tokens"] == 1
    assert out["ratio"] == 10.0
    assert out["latency_ms"] == 0.01


def test_native_route_maps_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def fake_decide(model_json: str, state_json: str) -> str:
        seen["model_json"] = model_json
        seen["state_json"] = state_json
        return json.dumps(
            {
                "action": "read_file",
                "confidence": 0.9,
                "reasoning": "",
                "latency_ms": 0.1,
            }
        )

    monkeypatch.setitem(sys.modules, "hive_cpp", SimpleNamespace(rust_router_decide=fake_decide))

    out = native_route({"goal": "fix the bug", "step": 3}, MINIMAL_MODEL_JSON)

    assert isinstance(seen["model_json"], str) and isinstance(seen["state_json"], str)
    assert out["tool"] == "read_file"
    assert out["action"] == "read_file"
    assert out["args"] == {}
    assert out["confidence"] == 0.9
    assert out["escalated"] is False


def test_native_route_escalation_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "hive_cpp",
        SimpleNamespace(
            rust_router_decide=lambda model_json, state_json: json.dumps(
                {"action": "escalate", "confidence": 0.0, "reasoning": "unknown state"}
            )
        ),
    )

    out = native_route({}, MINIMAL_MODEL_JSON)

    assert out["tool"] == "escalate"
    assert out["escalated"] is True


def test_native_route_fills_missing_state_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def fake_decide(model_json: str, state_json: str) -> str:
        captured["state_json"] = state_json
        return json.dumps({"action": "read_file", "confidence": 1.0, "reasoning": ""})

    monkeypatch.setitem(sys.modules, "hive_cpp", SimpleNamespace(rust_router_decide=fake_decide))

    native_route({"goal": "only the goal"}, MINIMAL_MODEL_JSON)

    state = json.loads(captured["state_json"])
    assert set(state) == AGENT_STATE_FIELDS
    assert state["goal"] == "only the goal"
    assert state["step"] == 0
    assert state["last_tool"] is None
    assert state["recent_observations"] == []


def test_native_backend_end_to_end() -> None:
    if not _require_native():
        pytest.skip("hive_cpp not built; set HIVE_REQUIRE_NATIVE=1 to require it")

    pytest.importorskip("hive_cpp")
    from hive.rule_fast import RuleFastHoneyComb
    from hive.stack import HiveStack

    stack = HiveStack(
        backend="native",
        honey_comb=RuleFastHoneyComb(),
        native_route_model=MINIMAL_MODEL_JSON,
    )

    raw = "DEBUG: cache miss\n" * 200
    compressed = stack.compress("tool", raw)
    assert compressed.content != raw
    assert compressed.original_tokens > 0

    decision = stack.route({"goal": "read the file", "step": 1})
    assert decision.tool == "read_file"
    assert decision.source == "busybee"
