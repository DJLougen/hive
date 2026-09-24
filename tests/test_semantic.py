"""Semantic routing layer: protocol, adapters, policy, cascade, factory.

All Jev responses are mocked — normal CI never touches the paid API.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hive.cascade_policy import CascadeRoutingPolicy
from hive.djeff_backend import DJeffBackend
from hive.jev_backend import JevBackend
from hive.semantic_backend import SemanticBackendError, validate_response
from hive.semantic_factory import build_semantic_stack, make_backend
from hive.semantic_policy import SemanticRoutingPolicy
from hive.semantic_schema import SCHEMA_VERSION, routing_questions
from hive.semantic_state import compile_state

# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #

def _resp(*, tool="read_file", probs=None, safe=0.99, reasoning=0.01, needs_llm=0.01,
          backend="jev", revision="jev-test"):
    return {
        "answers": {
            "tool": {"type": "choice", "choice": tool,
                     "probabilities": probs or {tool: 0.99}},
            "safe_to_execute": {"type": "noul", "noul": safe},
            "requires_reasoning": {"type": "noul", "noul": reasoning},
            "needs_llm": {"type": "noul", "noul": needs_llm},
        },
        # raw-body shape: JevBackend reads "model" from the response body
        "model": revision,
        "backend": backend,
        "latency_ms": 1.0,
    }


class _FakeClient:
    """Stand-in transport. Returns a queued response or raises."""

    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def post(self, payload):
        self.calls.append(payload)
        if self.error:
            raise self.error
        return self.response


READY_STATE = {
    "goal": "fix auth",
    "listed": True,
    "tests_run": 1,
    "tests_passed": False,
    "writes": 0,
    "files_read": ["tests/test_auth.py"],
    "suggested_read": "auth/session.py",
    "step": 3,
    "last_tool": "run_tests",
}


# --------------------------------------------------------------------------- #
# Schema / state
# --------------------------------------------------------------------------- #

def test_schema_is_versioned_and_stable():
    assert SCHEMA_VERSION == "hive-routing-v1"
    q = routing_questions()
    assert set(q) == {"tool", "safe_to_execute", "requires_reasoning", "needs_llm"}
    assert q["tool"]["type"] == "choice"
    assert routing_questions() is not q  # fresh copy, not shared mutable state


def test_compile_state_is_structural_and_bounded():
    s = compile_state({**READY_STATE, "files_read": [f"f{i}.py" for i in range(100)]},
                      max_chars=400, max_files=5)
    assert s["workflow"]["step"] == 3
    assert len(s["known_files"]) <= 5
    assert s["available_tools"]
    import json
    assert len(json.dumps(s)) <= 800


# --------------------------------------------------------------------------- #
# Normalization + validation
# --------------------------------------------------------------------------- #

def test_jev_normalizes_a_good_response():
    be = JevBackend(client=_FakeClient(_resp()))
    out = be.decide(state={"goal": "x"}, questions=routing_questions())
    assert out["backend"] == "jev"
    assert out["answers"]["tool"]["choice"] == "read_file"
    assert out["model_revision"] == "jev-test"


@pytest.mark.parametrize("bad", [
    None,
    "not-a-dict",
    {},
    {"answers": {}},
    {"answers": {"tool": {"type": "choice", "probabilities": {}}}},  # empty
    {"answers": {"tool": {"type": "noul", "noul": 0.5}}},            # wrong type
])
def test_validate_response_rejects_malformed(bad):
    with pytest.raises(SemanticBackendError):
        validate_response(bad, backend="test")


def test_jev_malformed_body_raises_not_routes():
    be = JevBackend(client=_FakeClient({"answers": {}}))
    with pytest.raises(SemanticBackendError):
        be.decide(state={"goal": "x"}, questions=routing_questions())


def test_jev_transport_error_becomes_backend_error():
    be = JevBackend(client=_FakeClient(error=OSError("boom")))
    with pytest.raises(SemanticBackendError):
        be.decide(state={"goal": "x"}, questions=routing_questions())


# --------------------------------------------------------------------------- #
# Policy: acceptance, determinism, failure
# --------------------------------------------------------------------------- #

def _policy(client, **kw):
    return SemanticRoutingPolicy(JevBackend(client=client), **kw)


def test_policy_accepts_a_confident_resolvable_decision():
    p = _policy(_FakeClient(_resp(tool="read_file", probs={"read_file": 0.97,
                                                           "grep": 0.03})))
    d = p.predict(READY_STATE)
    assert d["tool"] == "read_file"
    assert d["args"] == {"path": "auth/session.py"}   # deterministic resolver
    assert d["escalated"] is False
    assert d["source"] == "semantic:jev"


def test_policy_escalates_below_tool_threshold():
    p = _policy(_FakeClient(_resp(tool="read_file", probs={"read_file": 0.55,
                                                           "grep": 0.45})))
    d = p.predict(READY_STATE)
    assert d["escalated"] is True
    assert "below acceptance" in d["args"]["reason"]


def test_policy_escalates_when_unsafe():
    p = _policy(_FakeClient(_resp(safe=0.40)))
    assert p.predict(READY_STATE)["escalated"] is True


def test_policy_ignores_model_safety_and_defers_to_resolver():
    """safe_to_execute=1.0 must NOT let a write through without a recalled fix."""
    p = _policy(_FakeClient(_resp(tool="write_file", probs={"write_file": 0.99},
                                  safe=1.0)))
    d = p.predict(READY_STATE)
    assert d["escalated"] is True
    assert "cannot resolve args" in d["args"]["reason"]


def test_policy_escalates_on_backend_failure():
    p = _policy(_FakeClient(error=SemanticBackendError("timeout")))
    d = p.predict(READY_STATE)
    assert d["escalated"] is True
    assert "backend error" in d["args"]["reason"]
    assert p.stats["errors"] == 1


def test_policy_records_every_decision():
    seen = []
    p = _policy(_FakeClient(_resp()), record_sink=seen.append)
    p.predict({**READY_STATE, "task_id": "t1"})
    assert len(seen) == 1
    assert seen[0]["schema_version"] == SCHEMA_VERSION
    assert seen[0]["group_id"] == "t1"
    assert seen[0]["verdict"] == "accepted"


def test_record_sink_failure_does_not_break_routing():
    def boom(_):
        raise RuntimeError("sink down")

    p = _policy(_FakeClient(_resp()), record_sink=boom)
    assert p.predict(READY_STATE)["tool"] == "read_file"


# --------------------------------------------------------------------------- #
# Cascade
# --------------------------------------------------------------------------- #

class _FixedPolicy:
    def __init__(self, decision):
        self.decision = decision
        self.calls = 0

    def predict(self, state):
        self.calls += 1
        return dict(self.decision)


_ROUTE = {"tool": "run_tests", "args": {}, "escalated": False,
          "confidence": 1.0, "source": "cpu"}
_ESC = {"tool": "escalate", "args": {"reason": "unclear"}, "escalated": True,
        "confidence": 0.0, "source": "cpu"}


def test_cascade_fast_without_semantic_when_confident():
    fast = _FixedPolicy(_ROUTE)
    sem = _policy(_FakeClient(_resp()))
    c = CascadeRoutingPolicy(fast_policy=fast, semantic_policy=sem, mode="cascade")
    assert c.predict(READY_STATE)["source"] == "cpu"
    assert sem.stats["calls"] == 0  # semantic never consulted


def test_cascade_consults_semantic_on_escalation():
    c = CascadeRoutingPolicy(fast_policy=_FixedPolicy(_ESC),
                             semantic_policy=_policy(_FakeClient(_resp())),
                             mode="cascade")
    d = c.predict(READY_STATE)
    assert d["source"] == "semantic:jev"
    assert c.stats["semantic_accepted"] == 1


def test_shadow_mode_cannot_alter_execution():
    fast = _FixedPolicy(_ROUTE)
    sem = _policy(_FakeClient(_resp()))
    c = CascadeRoutingPolicy(fast_policy=fast, semantic_policy=sem, mode="shadow")
    d = c.predict(READY_STATE)
    assert d["source"] == "cpu"          # unchanged by the semantic layer
    assert sem.stats["calls"] == 1       # but it was observed
    assert c.stats["semantic_shadow_only"] == 1


@pytest.mark.parametrize("response", [
    _resp(),
    _resp(safe=0.1),
    {"answers": {}},
])
def test_shadow_preserves_fast_escalation(response):
    sem = _policy(_FakeClient(response))
    c = CascadeRoutingPolicy(fast_policy=_FixedPolicy(_ESC),
                             semantic_policy=sem, mode="shadow")
    assert c.predict(READY_STATE) == _ESC
    assert sem.stats["calls"] == 1
    assert c.stats["semantic_shadow_only"] == 1
    assert c.stats["semantic_accepted"] == 0
    assert c.stats["escalated"] == 1

def test_shadow_mode_semantic_error_and_refusal_do_not_alter_execution():
    """In shadow mode the semantic layer is observational: backend errors and
    threshold refusals must leave the fast route untouched."""
    for client in (_FakeClient(error=SemanticBackendError("down")),
                   _FakeClient(_resp(safe=0.0))):
        sem = _policy(client)
        c = CascadeRoutingPolicy(fast_policy=_FixedPolicy(_ROUTE),
                                 semantic_policy=sem, mode="shadow")
        assert c.predict(READY_STATE) == _ROUTE
        assert sem.stats["calls"] == 1
        assert c.stats["semantic_shadow_only"] == 1
        assert c.stats["semantic_accepted"] == 0


def test_shadow_mode_raising_policy_cannot_break_fast_route():
    """A duck-typed shadow policy that raises must not interfere either."""
    class _Raising:
        def predict(self, state):
            raise RuntimeError("boom")

    c = CascadeRoutingPolicy(fast_policy=_FixedPolicy(_ROUTE),
                             semantic_policy=_Raising(), mode="shadow")
    assert c.predict(READY_STATE) == _ROUTE
    assert c.stats["semantic_shadow_only"] == 1


def test_off_mode_never_calls_semantic():
    sem = _policy(_FakeClient(_resp()))
    c = CascadeRoutingPolicy(fast_policy=_FixedPolicy(_ESC), semantic_policy=sem, mode="off")
    d = c.predict(READY_STATE)
    assert d["escalated"] is True
    assert sem.stats["calls"] == 0


def test_cascade_bypasses_generation_states():
    gen = {"tool": "escalate", "args": {"reason": "generation required"},
           "escalated": True, "confidence": 0.0, "source": "cpu"}
    sem = _policy(_FakeClient(_resp()))
    c = CascadeRoutingPolicy(fast_policy=_FixedPolicy(gen), semantic_policy=sem, mode="cascade")
    c.predict(READY_STATE)
    assert sem.stats["calls"] == 0
    assert c.stats["generation_bypass"] == 1


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        CascadeRoutingPolicy(fast_policy=_FixedPolicy(_ROUTE), mode="nonsense")


# --------------------------------------------------------------------------- #
# d-Jeff placeholder + factory
# --------------------------------------------------------------------------- #

def test_djeff_without_checkpoint_fails_safe():
    with pytest.raises(SemanticBackendError, match="not configured"):
        DJeffBackend().decide(state={}, questions=routing_questions())


def test_djeff_with_client_validates_and_normalizes():
    class _Client:
        def post(self, payload):
            return _resp(backend=None)

    out = DJeffBackend(client=_Client(), model="djeff-x").decide(
        state={}, questions=routing_questions())
    assert out["backend"] == "djeff"
    assert out["model_revision"] == "djeff-x"


def test_factory_off_returns_fast_policy_unchanged():
    class _Cfg:
        semantic_enabled = False
        semantic_mode = "off"

    fast = _FixedPolicy(_ROUTE)
    assert build_semantic_stack(_Cfg(), fast_policy=fast) is fast


def test_factory_unknown_backend_raises():
    with pytest.raises(SemanticBackendError):
        make_backend("nope")


def test_factory_builds_cascade_with_injected_client(monkeypatch):
    class _Cfg:
        semantic_enabled = True
        semantic_mode = "cascade"
        semantic_primary = "jev"
        jev_model = "jev-test"

    monkeypatch.setenv("HIVE_JEV_API_KEY", "test-key")
    stack = build_semantic_stack(_Cfg(), fast_policy=_FixedPolicy(_ESC),
                                 clients={"jev": _FakeClient(_resp())})
    assert isinstance(stack, CascadeRoutingPolicy)
    assert stack.predict(READY_STATE)["source"] == "semantic:jev"


# --------------------------------------------------------------------------- #
# Compare mode + record sink + export
# --------------------------------------------------------------------------- #

def test_compare_mode_logs_both_backends_but_only_primary_routes():
    primary = _policy(_FakeClient(_resp(tool="read_file", probs={"read_file": 0.97})))
    shadow = _policy(_FakeClient(_resp(tool="grep", probs={"grep": 0.97}, backend="djeff")))
    c = CascadeRoutingPolicy(fast_policy=_FixedPolicy(_ESC), semantic_policy=primary,
                             shadow_semantic_policy=shadow, mode="compare")
    d = c.predict(READY_STATE)
    assert d["source"] == "semantic:jev"          # primary decided
    assert primary.stats["calls"] == 1
    assert shadow.stats["calls"] == 1             # shadow observed the same state
    assert c.stats["shadow_compared"] == 1


def test_compare_mode_never_lets_shadow_change_the_route():
    primary = _policy(_FakeClient(_resp(tool="read_file", probs={"read_file": 0.55})))  # rejects
    shadow = _policy(_FakeClient(_resp(tool="read_file", probs={"read_file": 0.99})))
    c = CascadeRoutingPolicy(fast_policy=_FixedPolicy(_ESC), semantic_policy=primary,
                             shadow_semantic_policy=shadow, mode="compare")
    assert c.predict(READY_STATE)["escalated"] is True


def test_compare_without_shadow_is_harmless():
    c = CascadeRoutingPolicy(fast_policy=_FixedPolicy(_ROUTE),
                             semantic_policy=_policy(_FakeClient(_resp())),
                             shadow_semantic_policy=None, mode="compare")
    assert c.predict(READY_STATE)["tool"] == "run_tests"

def test_compare_shadow_error_does_not_interfere_with_primary():
    """A shadow backend that errors (or raises) must not change the primary
    route in compare mode."""
    primary = _policy(_FakeClient(_resp(tool="read_file",
                                        probs={"read_file": 0.97})))
    shadow = _policy(_FakeClient(error=SemanticBackendError("down")))
    c = CascadeRoutingPolicy(fast_policy=_FixedPolicy(_ESC),
                             semantic_policy=primary,
                             shadow_semantic_policy=shadow, mode="compare")
    d = c.predict(READY_STATE)
    assert d["tool"] == "read_file"          # primary route unaffected
    assert shadow.stats["calls"] == 1        # shadow was still asked
    # The policy converted the backend error into a normal escalation, so the
    # shadow call completed and counts as a comparison.
    assert c.stats["shadow_compared"] == 1

    class _Raising:
        def predict(self, state):
            raise RuntimeError("boom")

    c2 = CascadeRoutingPolicy(fast_policy=_FixedPolicy(_ESC),
                              semantic_policy=_policy(
                                  _FakeClient(_resp(tool="read_file",
                                                    probs={"read_file": 0.97}))),
                              shadow_semantic_policy=_Raising(), mode="compare")
    assert c2.predict(READY_STATE)["tool"] == "read_file"
    assert c2.stats["shadow_compared"] == 0


def test_factory_compare_mode_asks_shadow_backend(monkeypatch):
    class _Cfg:
        semantic_enabled = True
        semantic_mode = "compare"
        semantic_primary = "jev"
        semantic_shadow = "djeff"
        jev_model = "jev-test"
        djeff_model = "djeff-test"

    jev = _FakeClient(_resp(tool="read_file", probs={"read_file": 0.97}))
    djeff = _FakeClient(_resp(tool="grep", probs={"grep": 0.97}, backend="djeff"))
    monkeypatch.setenv("HIVE_JEV_API_KEY", "k")
    stack = build_semantic_stack(_Cfg(), fast_policy=_FixedPolicy(_ESC),
                                   clients={"jev": jev, "djeff": djeff})
    assert stack.mode == "compare"
    stack.predict(READY_STATE)
    assert len(jev.calls) == 1
    assert len(djeff.calls) == 1
    assert stack.stats["shadow_compared"] == 1


def test_factory_compare_requires_distinct_shadow(monkeypatch):
    class _Cfg:
        semantic_enabled = True
        semantic_mode = "compare"
        semantic_primary = "jev"
        semantic_shadow = "jev"   # same as primary
        jev_model = None

    monkeypatch.setenv("HIVE_JEV_API_KEY", "k")
    with pytest.raises(SemanticBackendError, match="must differ"):
        build_semantic_stack(_Cfg(), fast_policy=_FixedPolicy(_ROUTE),
                             clients={"jev": _FakeClient(_resp())})

def test_factory_compare_calls_both_backends_but_only_primary_routes(monkeypatch):
    """A factory-built compare stack must keep mode='compare': on fast
    escalation both injected clients are asked, and only the primary routes."""
    class _Cfg:
        semantic_enabled = True
        semantic_mode = "compare"
        semantic_primary = "jev"
        semantic_shadow = "djeff"
        jev_model = "jev-test"
        djeff_model = "djeff-x"

    jev_client = _FakeClient(_resp(tool="read_file", probs={"read_file": 0.97}))
    djeff_client = _FakeClient(_resp(tool="grep", probs={"grep": 0.97},
                                     backend="djeff"))
    monkeypatch.setenv("HIVE_JEV_API_KEY", "test-key")
    stack = build_semantic_stack(
        _Cfg(), fast_policy=_FixedPolicy(_ESC),
        clients={"jev": jev_client, "djeff": djeff_client})

    d = stack.predict(READY_STATE)
    assert d["source"] == "semantic:jev"      # primary decided the route
    assert d["tool"] == "read_file"           # shadow's "grep" never applied
    assert len(jev_client.calls) == 1
    assert len(djeff_client.calls) == 1       # shadow actually consulted
    assert stack.stats["shadow_compared"] == 1


def test_jsonl_sink_round_trips_and_export_keeps_teacher_and_label_separate(tmp_path):
    import json
    import subprocess
    import sys

    from hive.semantic_records import JsonlRecordSink, comparison_record

    sink = JsonlRecordSink(tmp_path / "recs.jsonl")
    for ep in ("e1", "e2", "e3"):
        for step in range(4):
            sink(comparison_record(
                state={"step": step}, questions={"tool": {}},
                prediction={"tool": {"probabilities": {"read_file": 0.9}},
                            "safe_to_execute": {"noul": 0.2}},
                backend="jev", model_revision="jev-x", selected_tool="read_file",
                accepted=True, actual_action="read_file", outcome="correct", group_id=ep))
    assert sink.count == 12

    out = tmp_path / "corpus.jsonl"
    r = subprocess.run(
        [sys.executable, "scripts/export_semantic_training_data.py",
         "--records", str(tmp_path / "recs.jsonl"), "--out", str(out), "--seed", "0"],
        capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent))
    assert r.returncode == 0, r.stderr
    examples = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(examples) == 12
    e = examples[0]
    assert "teacher" in e and "label" in e          # never merged
    assert e["target_provenance"]["soft_distribution"] == "jev_teacher"
    assert e["target_provenance"]["actual_action"] == "agent_execution"
    # group-wise split: no group may appear in two splits
    by_group: dict[str, set[str]] = {}
    for ex in examples:
        by_group.setdefault(ex["group_id"], set()).add(ex["split"])
    assert all(len(s) == 1 for s in by_group.values())
