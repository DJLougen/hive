"""Regression tests for the code-review findings (see hive-review.md).

Each test maps to a verified bug that previously failed or silently
dropped data: supersede chain reachability, async remember kwargs,
validate wiring, gossip causal propagation, config wiring, is_winner
after rollback, real model signatures, probe URL validation, and
feedback ordering.
"""

from __future__ import annotations

import pytest

from hive import HiveStack
from hive.rule_fast import RuleFastHoneyComb
from hive.rust_brain import EdgeKind, RustBrain

# ---------------------------------------------------------------------------
# supersede: chain must be walkable from the live node
# ---------------------------------------------------------------------------

def test_supersede_chain_is_reachable_via_neighbours():
    brain = RustBrain()
    brain.remember("endpoint_health", {"status": 500, "cause": "pool exhausted"})
    brain.supersede("endpoint_health", {"status": 200, "fix": "max_connections=200"})

    # The documented provenance walk: neighbours(key, "supersedes") reports
    # that a prior version exists.
    assert brain.neighbours("endpoint_health", "supersedes") == ["endpoint_health"]

    # The actual prior version is retrievable via history().
    prior = brain.history("endpoint_health")
    assert len(prior) == 1
    assert prior[0].value["status"] == 500


def test_supersede_multi_step_chain():
    brain = RustBrain()
    brain.remember("k", "v1")
    brain.supersede("k", "v2")
    brain.supersede("k", "v3")
    versions = [n.value for n in brain.history("k")]
    assert versions == ["v1", "v2"]
    assert brain.recall("k") == "v3"


def test_supersede_history_survives_snapshot_roundtrip(tmp_path):
    brain = RustBrain()
    brain.remember("k", "v1")
    brain.supersede("k", "v2")
    path = str(tmp_path / "snap.gz")
    brain.snapshot_to_file(path)

    brain2 = RustBrain()
    brain2.restore_from_file(path)
    assert brain2.recall("k") == "v2"
    assert [n.value for n in brain2.history("k")] == ["v1"]
    assert brain2.neighbours("k", EdgeKind.SUPERSEDES) == ["k"]


def test_forget_and_evict_drop_history():
    brain = RustBrain(max_nodes=3)
    brain.remember("k", "v1")
    brain.supersede("k", "v2")
    brain.forget("k")
    assert brain.history("k") == []

    brain2 = RustBrain(max_nodes=2)
    brain2.remember("a", 1)
    brain2.supersede("a", 2)
    brain2.remember("b", 1)
    brain2.remember("c", 1)  # evicts "a" (oldest)
    assert brain2.history("a") == []


# ---------------------------------------------------------------------------
# restore: persisted HLC must be kept and the process clock advanced
# ---------------------------------------------------------------------------

def test_restore_preserves_hlc_and_advances_clock():
    brain = RustBrain()
    brain.remember("k", "v", hlc=(1_000_000, 7, "nodeX"))
    node = brain.get("k")
    assert node.hlc == (1_000_000, 7, "nodeX")

    # A subsequent write must dominate the restored timestamp.
    new_node = brain.remember("k2", "v2")
    assert new_node.hlc > (1_000_000, 7, "nodeX")


# ---------------------------------------------------------------------------
# async stack: trust/tags/caused_by must reach the store; step() parity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_async_remember_forwards_trust_tags_caused_by():
    from hive.async_stack import AsyncHiveStack

    stack = AsyncHiveStack()
    await stack.remember("parent", "p")
    node = await stack.remember(
        "child", "c", trust=0.4, tags=("t1",), caused_by=("parent",)
    )
    assert node.trust == pytest.approx(0.4)
    assert node.tags == {"t1"}
    assert "parent" in node.edges.get(EdgeKind.CAUSED_BY, set())


@pytest.mark.asyncio
async def test_async_step_matches_sync_contract():
    from hive.async_stack import AsyncHiveStack

    stack = AsyncHiveStack(honey_comb=RuleFastHoneyComb())
    out = await stack.step(
        {"step": 3, "goal": "x", "available_tools": []},
        [("user", "hi"), ("tool", "obs")],
    )
    assert set(out) == {"decision", "compressed", "stats"}
    assert out["compressed"] is not None
    assert out["compressed"].role == "tool"  # last turn only, like sync
    assert "brain" in out["stats"]
    assert await stack.recall("decision:3") is not None


# ---------------------------------------------------------------------------
# validate=True must actually validate
# ---------------------------------------------------------------------------

def test_validate_rejects_bad_memory_write():
    stack = HiveStack(honey_comb=RuleFastHoneyComb(), validate=True)
    with pytest.raises(ValueError):
        stack.remember("", "v")  # empty key rejected
    with pytest.raises(ValueError):
        stack.remember("k", "v", trust=2.0)  # trust out of range


def test_validate_normalizes_state_before_route():
    stack = HiveStack(honey_comb=RuleFastHoneyComb(), validate=True)
    stack.route({"goal": "x", "step": 0})
    # step default/fields populated by pydantic before predict()
    assert stack._last_state["step"] == 0


def test_validate_rejects_negative_step_in_route():
    stack = HiveStack(honey_comb=RuleFastHoneyComb(), validate=True)
    with pytest.raises(ValueError):
        stack.route({"goal": "x", "step": -1})


# ---------------------------------------------------------------------------
# config wiring: max_memory_nodes + rate_limit now take effect
# ---------------------------------------------------------------------------

def test_config_max_memory_nodes_reaches_brain():
    from hive.config import HiveConfig

    stack = HiveStack(
        honey_comb=RuleFastHoneyComb(), config=HiveConfig(max_memory_nodes=5)
    )
    assert stack.brain._max_nodes == 5


def test_config_rate_limit_creates_limiter():
    from hive.config import HiveConfig

    stack = HiveStack(
        honey_comb=RuleFastHoneyComb(), config=HiveConfig(rate_limit=2)
    )
    assert stack.rate_limiter is not None
    stack.route({"goal": "a"})
    stack.route({"goal": "b"})
    d = stack.route({"goal": "c"})
    assert d.source == "ratelimit"


def test_config_to_dict_redacts_jwt_secret():
    from hive.config import HiveConfig

    cfg = HiveConfig(jwt_secret="hunter2")
    assert cfg.to_dict()["jwt_secret"] == "***"


# ---------------------------------------------------------------------------
# gossip: causal edges, ts/hlc propagation, optional token auth
# ---------------------------------------------------------------------------

def test_gossip_preserves_causality_and_clock():
    from hive.gossip import GossipProtocol

    src = RustBrain()
    dst = RustBrain()
    g_src = GossipProtocol(src, peers=[])
    g_dst = GossipProtocol(dst, peers=[])

    n1 = src.remember("a", 1, hlc=(500, 0, "n1"))
    n2 = src.remember(
        "b", 2, edges={"caused_by": ["a"]}, trust=0.5, tags=("x",),
        hlc=(501, 0, "n1"),
    )
    g_src.publish(n1.to_dict())
    g_src.publish(n2.to_dict())
    batch = [g_src._queue.get_nowait(), g_src._queue.get_nowait()]

    assert g_dst.receive(batch) == 2
    node_b = dst.get("b")
    assert node_b.trust == pytest.approx(0.5)
    assert node_b.tags == {"x"}
    assert node_b.hlc == (501, 0, "n1")
    assert "a" in node_b.edges["caused_by"]
    # A new local write must dominate the received HLC.
    fresh = dst.remember("c", 3)
    assert fresh.hlc > (501, 0, "n1")


def test_gossip_receive_requires_token_when_configured():
    from hive.gossip import GossipProtocol

    dst = RustBrain()
    g = GossipProtocol(dst, peers=[], token="s3cret")
    with pytest.raises(PermissionError):
        g.receive([{"key": "k", "value": "v"}], token="wrong")
    with pytest.raises(PermissionError):
        g.receive([{"key": "k", "value": "v"}])
    assert g.receive([{"key": "k", "value": "v"}], token="s3cret") == 1


# ---------------------------------------------------------------------------
# ab_test: rollback is not a win
# ---------------------------------------------------------------------------

def test_is_winner_false_after_rollback():
    from hive.ab_test import ABTestHarness

    class P:
        def predict(self, state):
            return {"tool": "t", "confidence": 0.5}

    ab = ABTestHarness(control=P(), variant=P())
    assert ab.is_winner() is False
    ab.rollback()
    assert ab.is_winner() is False
    ab.promote_variant()
    assert ab.is_winner() is True


# ---------------------------------------------------------------------------
# model registry: real Ed25519 signatures
# ---------------------------------------------------------------------------

def test_signed_model_loads_and_forgery_fails(tmp_path):
    pytest.importorskip("cryptography")
    joblib = pytest.importorskip("joblib")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    from hive.model_registry import ModelRegistry, UnsignedModelError

    model_path = tmp_path / "m.joblib"
    joblib.dump({"weights": [1, 2, 3]}, model_path)

    key = Ed25519PrivateKey.generate()
    sig_doc = ModelRegistry.sign_model(model_path, key)

    reg = ModelRegistry(strict=True)
    reg.trust_signer(sig_doc["fingerprint"])
    loaded = reg.load(model_path)
    assert loaded == {"weights": [1, 2, 3]}

    # Untrusted signer fingerprint → reject.
    reg2 = ModelRegistry(strict=True)
    with pytest.raises(UnsignedModelError):
        reg2.load(model_path)

    # Tampered model (hash file still matches? rewrite hash to match but
    # signature stays over the old digest → signature verify fails).
    joblib.dump({"weights": ["evil"]}, model_path)
    import hashlib

    new_digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
    model_path.with_suffix(".joblib.sha256").write_text(new_digest)
    with pytest.raises(UnsignedModelError):
        reg.load(model_path)


def test_legacy_fingerprint_only_sig_rejected_in_strict(tmp_path):
    joblib = pytest.importorskip("joblib")
    import hashlib
    import json

    from hive.model_registry import ModelRegistry, UnsignedModelError

    model_path = tmp_path / "m.joblib"
    joblib.dump({"a": 1}, model_path)
    digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
    model_path.with_suffix(".joblib.sha256").write_text(digest)
    model_path.with_suffix(".joblib.sig").write_text(
        json.dumps({"fingerprint": "AABBCC"})
    )
    reg = ModelRegistry(strict=True)
    reg.trust_signer("AABBCC")
    with pytest.raises(UnsignedModelError):
        reg.load(model_path)


# ---------------------------------------------------------------------------
# llm.probe_endpoint: non-http schemes must be rejected
# ---------------------------------------------------------------------------

def test_probe_endpoint_rejects_non_http_scheme():
    from hive.llm import probe_endpoint

    with pytest.raises(ValueError):
        probe_endpoint("file:///etc/passwd")
    with pytest.raises(ValueError):
        probe_endpoint("ftp://x")


# ---------------------------------------------------------------------------
# feedback: out-of-order decisions accepted within the pending window
# ---------------------------------------------------------------------------

def test_record_outcome_accepts_recent_non_latest_decision():
    from hive.feedback import FeedbackBuffer, OutcomeType

    fb = FeedbackBuffer(capacity=10)
    stack = HiveStack(honey_comb=RuleFastHoneyComb(), feedback_buffer=fb)
    d1 = stack.route({"goal": "g1"})
    d2 = stack.route({"goal": "g2"})
    # d1 is no longer the most recent, but is within the pending window —
    # its outcome records against the *first* state.
    stack.record_outcome(d1, "escalate", OutcomeType.CORRECT)
    stack.record_outcome(d2, "escalate", OutcomeType.CORRECT)
    assert len(fb) == 2
    outcomes = fb.get_outcomes()
    assert outcomes[0].state["goal"] == "g1"
    assert outcomes[1].state["goal"] == "g2"


def test_record_outcome_binds_identical_decisions_by_object_identity():
    from hive.feedback import FeedbackBuffer, OutcomeType

    fb = FeedbackBuffer(capacity=10)
    stack = HiveStack(honey_comb=RuleFastHoneyComb(), feedback_buffer=fb)
    d1 = stack.route({"goal": "g1"})
    d2 = stack.route({"goal": "g2"})
    assert d1 == d2  # default fallback produces identical fields
    stack.record_outcome(d2, "escalate", OutcomeType.CORRECT)
    assert fb.get_outcomes()[-1].state["goal"] == "g2"


def test_record_outcome_rejects_ambiguous_reconstructed_decision():
    from hive.feedback import FeedbackBuffer, OutcomeType
    from hive.stack import RouteDecision

    fb = FeedbackBuffer(capacity=10)
    stack = HiveStack(honey_comb=RuleFastHoneyComb(), feedback_buffer=fb)
    d1 = stack.route({"goal": "g1"})
    d2 = stack.route({"goal": "g2"})
    assert d1 == d2
    reconstructed = RouteDecision(
        tool=d1.tool,
        args=dict(d1.args),
        confidence=d1.confidence,
        escalated=d1.escalated,
        source=d1.source,
    )
    stack.record_outcome(reconstructed, "escalate", OutcomeType.CORRECT)
    assert len(fb) == 0


# ---------------------------------------------------------------------------
# step(): states without "step" must not clobber decision:0
# ---------------------------------------------------------------------------

def test_step_uses_monotonic_key_when_step_missing():
    stack = HiveStack(honey_comb=RuleFastHoneyComb())
    stack.step({"goal": "a"}, [("user", "hi")])
    stack.step({"goal": "b"}, [("user", "yo")])
    assert stack.recall("decision:0") is not None
    assert stack.recall("decision:1") is not None


# ---------------------------------------------------------------------------
# streaming: no fabricated "busybee" source
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_router_reports_real_source():
    from hive.streaming import StreamRouter

    stack = HiveStack(honey_comb=RuleFastHoneyComb())
    chunks = [
        c
        async for c in StreamRouter(stack).route_stream({"goal": "x"})
    ]
    routing = next(c for c in chunks if c["stage"] == "routing")
    assert "source" not in routing  # no fabricated source pre-decision
    decision = next(c for c in chunks if c["stage"] == "decision")
    assert decision["source"] == "fallback"  # no busybee policy → fallback


# ---------------------------------------------------------------------------
# gossip: remember() publishes to attached GossipProtocol
# ---------------------------------------------------------------------------

def test_remember_publishes_to_gossip():
    from hive.gossip import GossipProtocol

    src = RustBrain()
    dst = RustBrain()
    g_src = GossipProtocol(src, peers=[])
    g_dst = GossipProtocol(dst, peers=[])
    stack = HiveStack(
        honey_comb=RuleFastHoneyComb(), rust_brain=src, gossip=g_src
    )

    stack.remember("k", {"v": 1}, trust=0.5, tags=("t",), caused_by=("root",))
    batch = [g_src._queue.get_nowait()]
    assert g_dst.receive(batch) == 1
    node = dst.get("k")
    assert node.value == {"v": 1}
    assert node.trust == pytest.approx(0.5)
    assert node.tags == {"t"}
    assert "root" in node.edges["caused_by"]


def test_remember_works_when_gossip_publish_fails():
    class BrokenGossip:
        def publish(self, event):
            raise RuntimeError("peer down")

    stack = HiveStack(
        honey_comb=RuleFastHoneyComb(), gossip=BrokenGossip()
    )
    node = stack.remember("k", "v")
    assert node.key == "k"


# ---------------------------------------------------------------------------
# audit_enabled: stack captures an auditable event trail
# ---------------------------------------------------------------------------

def test_audit_events_captured_when_enabled():
    from hive.config import HiveConfig
    from hive.feedback import FeedbackBuffer, OutcomeType
    from hive.stack import RouteDecision

    stack = HiveStack(
        honey_comb=RuleFastHoneyComb(),
        feedback_buffer=FeedbackBuffer(capacity=4),
        config=HiveConfig(audit_enabled=True),
        tenant_id="acme",
    )
    assert stack.audit_events() == []
    d = stack.route({"goal": "g"})
    stack.remember("k", "v")
    stack.record_outcome(d, "escalate", OutcomeType.CORRECT)
    # A forged decision also lands in the trail (policy-poisoning signal).
    fake = RouteDecision(tool="x", args={}, confidence=1.0, escalated=False, source="busybee")
    stack.record_outcome(fake, "x", OutcomeType.CORRECT)

    actions = [e["action"] for e in stack.audit_events()]
    assert actions == [
        "route", "remember", "record_outcome", "record_outcome_rejected"
    ]
    assert all(e["tenant_id"] == "acme" for e in stack.audit_events())


def test_audit_disabled_by_default():
    stack = HiveStack(honey_comb=RuleFastHoneyComb())
    stack.route({"goal": "g"})
    stack.remember("k", "v")
    assert stack.audit_events() == []
