"""Harness helpers — load routing policies and wire Hive into eval harnesses."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

_log = logging.getLogger(__name__)


class RoutingPolicy(Protocol):
    """Minimal interface expected by :class:`hive.stack.HiveStack`."""

    def predict(self, state: dict[str, Any]) -> dict[str, Any]: ...


class RuleBasedRoutingPolicy:
    """Rule-based policy that routes mechanical agent-loop decisions to the CPU.

    Two decision layers, evaluated in order:

       signals an agent loop produces (``listed``, ``tests_run``,
       ``tests_passed``, ``suggested_read``, ``writes`` …), the policy routes
       the canonical mechanical transitions locally: enumerate the repo,
       reproduce the failure, read the file the traceback names, re-verify
       after a write. ``finish`` is deliberately *not* routed — done-ness is
       a judgment about the spec, not a mechanical transition, so a green
       verify escalates to the model. Everything else escalates to the LLM.

    2. **Keyword fallback** — for generic states without workflow signals,
       route obvious mechanical goals by keyword (read file, run tests,
       apply patch, install). Unknown goals escalate.
    """

    def __init__(self) -> None:
        self.stats = {"routed": 0, "escalated": 0}

    def _route(self, tool: str, args: dict[str, Any] | None = None, confidence: float = 0.95) -> dict[str, Any]:
        self.stats["routed"] += 1
        return {"tool": tool, "args": args or {}, "confidence": confidence, "escalated": False}

    def _escalate(self, reason: str) -> dict[str, Any]:
        self.stats["escalated"] += 1
        return {"tool": "escalate", "args": {"reason": reason}, "confidence": 0.5, "escalated": True}

    def predict(self, state: dict[str, Any]) -> dict[str, Any]:
        # Workflow layer: only engage when the caller exposes loop signals.
        if "listed" in state or "tests_run" in state:
            return self._predict_workflow(state)
        return self._predict_keywords(state)

    def _predict_workflow(self, state: dict[str, Any]) -> dict[str, Any]:
        listed = bool(state.get("listed"))
        tests_run = int(state.get("tests_run") or 0)
        writes = int(state.get("writes") or 0)
        tests_passed = state.get("tests_passed")  # None | True | False
        verify_pending = bool(state.get("verify_pending"))
        suggested_read = state.get("suggested_read")
        files_read = state.get("files_read") or []

        # Mechanical transitions, highest precedence first.
        if tests_passed is True and writes > 0:
            # Green tests + a patch on disk is where a mechanical policy used
            # to finish — and where an under-covered suite certifies a
            # partial fix. Done-ness is a judgment call about the *spec*, not
            # a mechanical transition, so it belongs to the model.
            return self._escalate(
                "verify green — confirm the spec is fully implemented before finishing")
        if verify_pending:
            return self._route("run_tests")  # verify the patch once, then re-diagnose
        if not listed:
            return self._route("list_files")
        if tests_run == 0:
            return self._route("run_tests")  # reproduce the bug
        if suggested_read and suggested_read not in files_read:
            return self._route("read_file", {"path": suggested_read})
        return self._escalate("diagnosis / patch synthesis needs reasoning")

    def _predict_keywords(self, state: dict[str, Any]) -> dict[str, Any]:
        combined = str(state.get("goal", "")).lower()

        if any(kw in combined for kw in ["read file", "read_file", "list dir", "grep", "search file", "view code"]):
            return self._route("read_file")
        if any(kw in combined for kw in ["run test", "pytest", "execute test", "check test"]):
            return self._route("run_tests")
        if any(kw in combined for kw in ["apply patch", "git apply", "apply diff", "write fix", "edit file"]):
            return self._route("apply_patch")
        if any(kw in combined for kw in ["install", "pip install", "setup"]):
            return self._route("run_command", confidence=0.90)

        return self._escalate("complex reasoning")


class EscalateOnlyPolicy:
    """Routes nothing: every decision goes back to the model.

    Exists so a run can keep Hive's compression + causal memory while removing
    routing, which separates "the policy decides for us" from "the context is
    smaller and replayable". Without this arm a Hive-vs-baseline difference has
    two candidate causes and no way to tell them apart.
    """

    def __init__(self) -> None:
        self.stats = {"routed": 0, "escalated": 0}

    def predict(self, state: dict[str, Any]) -> dict[str, Any]:
        self.stats["escalated"] += 1
        return {"tool": "escalate", "args": {"reason": "escalate-only control"},
                "confidence": 0.5, "escalated": True}


def load_routing_policy(*, model_path: str | Path | None = None) -> RoutingPolicy:
    """Return a trained busyBee policy when available, else a rule-based fallback."""
    if model_path is not None:
        try:
            from busybee_cpu import CpuActionPolicy  # type: ignore[import-not-found]

            policy = CpuActionPolicy.load(str(model_path))
            _log.info("Loaded busyBee model from %s", model_path)
            return policy
        except Exception as exc:
            _log.warning("Failed to load busyBee model %s: %s — using rule-based fallback", model_path, exc)

    try:
        from busybee_cpu import CpuActionPolicy  # type: ignore[import-not-found]

        _log.info("busyBee-cpu installed but no model path given — using rule-based fallback")
    except Exception:
        _log.info("busyBee-cpu not installed — using rule-based routing fallback")

    return RuleBasedRoutingPolicy()


def policy_label(policy: RoutingPolicy) -> str:
    """Human-readable label for logging and eval reports."""
    cls = type(policy).__name__
    if cls == "RuleBasedRoutingPolicy":
        return "rule-based"
    if cls == "EscalateOnlyPolicy":
        return "escalate-only"
    return cls
