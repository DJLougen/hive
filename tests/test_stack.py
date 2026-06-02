"""Tests for :mod:`hive.stack` and the in-repo rule-fast fallback."""

from __future__ import annotations


import pytest

from hive import HiveStack
from hive.rule_fast import (
    Label,
    Message,
    RuleFastHoneyComb,
    _compress_test_output,
    _infer_content_type,
)


def test_stack_with_fallback_compress():
    """HiveStack defaults to honey-comb when installed, but the rule_fast
    fallback must work end-to-end when explicitly passed in."""
    stack = HiveStack(honey_comb=RuleFastHoneyComb())
    out = stack.compress(
        "tool", "tests: 12 passed, 2 failed (test_session_invalidation)"
    )
    assert out.label == "distill"
    assert out.compressed_tokens <= out.original_tokens


def test_infer_content_type_test_output():
    content = "tests: 12 passed, 0 failed"
    assert _infer_content_type("tool", content) == "tool_result_test"


def test_infer_content_type_search_results():
    content = "src/auth.py:42: def login():\nsrc/auth.py:43:     return user"
    assert _infer_content_type("tool", content) == "tool_result_search"


def test_infer_content_type_file():
    content = (
        "import os\nclass Foo:\n    def bar(self):\n        return 1\n" + "x" * 1000
    )
    assert _infer_content_type("tool", content) == "tool_result_file"


def test_infer_content_type_error():
    content = "Traceback (most recent call last):\nValueError: bad"
    assert _infer_content_type("tool", content) == "tool_result_error"


def test_compress_test_output_collapses_lines():
    out = _compress_test_output("test_a ... ok\ntest_b ... FAIL\ntest_c ... ok")
    assert "FAIL" in out
    assert "12 passed" not in out  # we didn't claim that


def test_route_falls_back_when_no_policy():
    stack = HiveStack()
    decision = stack.route({"goal": "x", "state": {}, "available_tools": []})
    assert decision.tool == "escalate"
    assert decision.source == "fallback"
    assert decision.escalated is True


def test_remember_recall_roundtrip():
    stack = HiveStack(honey_comb=RuleFastHoneyComb())
    stack.remember("ep", "/v1/chat", trust=0.9)
    assert stack.recall("ep") == "/v1/chat"


def test_recall_telemetry_counts_none_value_as_hit():
    from hive.telemetry import Telemetry

    telemetry = Telemetry()
    stack = HiveStack(honey_comb=RuleFastHoneyComb(), telemetry=telemetry)
    stack.remember("nullable", None)
    stack.recall("nullable")
    assert telemetry.memory_reads[-1].hit is True


def test_step_writes_decision_to_brain():
    stack = HiveStack(honey_comb=RuleFastHoneyComb())
    result = stack.step(
        {"step": 7, "state": {"last_tool": None}, "available_tools": []},
        [("user", "do thing"), ("tool", "obs: ready")],
    )
    assert result["decision"].tool == "escalate"
    # decision was written to brain
    assert stack.recall("decision:7") is not None
    # last turn was compressed
    assert result["compressed"] is not None


def test_compress_many_preserves_order():
    stack = HiveStack(honey_comb=RuleFastHoneyComb())
    out = stack.compress_many(
        [
            ("user", "hi"),
            ("tool", "tests: 1 passed, 0 failed"),
            ("assistant", "ok"),
        ]
    )
    assert [c.role for c in out] == ["user", "tool", "assistant"]


def test_label_string_values_match_honeycomb():
    # If the honey-comb sibling is installed, the enum values should match
    # the rule-fast string values exactly. This is a forward-compat test.
    try:
        from honeycomb.labels import Label as HCLabel
    except Exception:
        pytest.skip("honeycomb not installed")
    assert HCLabel.CORE.value == Label.CORE
    assert HCLabel.DISTILL.value == Label.DISTILL
    assert HCLabel.COMPACT.value == Label.COMPACT
    assert HCLabel.DROP.value == Label.DROP
    assert HCLabel.STALE.value == Label.STALE
    assert HCLabel.ESCALATE.value == Label.ESCALATE


def test_rule_fast_throughput_smoke():
    """The rule-fast path must sustain >=2k msg/s on x86_64.

    The exact number depends on hardware; the goal of this test is to
    catch a regression that would put us below 2k msg/s.
    """
    import random
    import string
    import time

    hc = RuleFastHoneyComb()
    rng = random.Random(0)

    def synth() -> str:
        return " ".join(
            "".join(rng.choices(string.ascii_letters, k=6)) for _ in range(120)
        )

    for _ in range(50):  # warmup
        hc.process(Message(role="tool", content=synth()))
    t0 = time.perf_counter()
    for _ in range(2000):
        hc.process(Message(role="tool", content=synth()))
    elapsed = time.perf_counter() - t0
    rate = 2000 / elapsed
    # 2k msg/s is a conservative floor; production hardware easily does 5x.
    assert rate >= 2_000, f"rule_fast throughput {rate:.0f} msg/s below 2k floor"


def test_hive_stack_stats_shape():
    stack = HiveStack(honey_comb=RuleFastHoneyComb())
    stack.remember("k", "v")
    stack.compress("tool", "tests: 1 passed, 0 failed")
    stats = stack.stats()
    assert "brain" in stats
    assert "comb" in stats
    assert stats["brain"]["node_count"] == 1
