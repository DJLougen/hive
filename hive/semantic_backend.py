"""The semantic decision protocol.

Hive depends on *this* interface, never on a specific model. Jev is the first
implementation; a local d-Jeff backend will be the second, and nothing outside
:mod:`hive.semantic_policy` should need to know which is active.

A backend receives a compiled semantic state and a versioned question schema and
returns a **normalized** response::

    {
        "answers": {
            "tool":                {"type": "choice", "choice": "read_file",
                                    "probabilities": {"read_file": 0.74, ...}},
            "safe_to_execute":     {"type": "noul", "noul": 0.96},
            "requires_reasoning":  {"type": "noul", "noul": 0.08},
            "needs_llm":           {"type": "noul", "noul": 0.06},
        },
        "backend": "jev",
        "model_revision": "jev-1.13.0",
        "latency_ms": 12.3,
        "request_id": None,
        "raw": None,
    }

Backends raise :class:`SemanticBackendError` for any failure (timeout, HTTP
error, malformed body, schema mismatch). Callers treat that as "escalate to the
LLM" — a semantic failure must never be silently equivalent to a decision.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

#: Question ids every conforming backend must answer.
REQUIRED_QUESTIONS: tuple[str, ...] = (
    "tool",
    "safe_to_execute",
    "requires_reasoning",
    "needs_llm",
)


class SemanticBackendError(RuntimeError):
    """A semantic backend could not produce a usable decision.

    Raised for timeouts, transport errors, non-2xx responses, malformed or
    schema-violating bodies, and rate limits. The semantic policy converts this
    into LLM escalation; it is never swallowed into a routed decision.
    """


@runtime_checkable
class SemanticDecisionBackend(Protocol):
    """What the semantic routing layer requires of a backend."""

    #: Short stable name, used in telemetry labels (``semantic:jev``).
    backend_name: str

    #: Model revision the backend is pinned to, if it exposes one.
    model_revision: str | None

    def decide(
        self,
        *,
        state: dict[str, Any],
        questions: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Return a normalized decision for ``state`` under ``questions``.

        Must raise :class:`SemanticBackendError` rather than return a partial or
        guessed answer.
        """
        ...


def validate_response(resp: Any, *, backend: str = "?") -> dict[str, Any]:
    """Check a backend response against the normalized shape.

    Returns the response so it can be used inline. Raises
    :class:`SemanticBackendError` on any structural problem — this is the single
    place both adapters and the policy agree on what a valid answer looks like.
    """
    if not isinstance(resp, dict):
        raise SemanticBackendError(f"{backend}: response is {type(resp).__name__}, not a dict")

    answers = resp.get("answers")
    if not isinstance(answers, dict):
        raise SemanticBackendError(f"{backend}: missing 'answers' mapping")

    for qid in REQUIRED_QUESTIONS:
        answer = answers.get(qid)
        if not isinstance(answer, dict):
            raise SemanticBackendError(f"{backend}: answer {qid!r} missing or not a mapping")
        kind = answer.get("type")
        if qid == "tool":
            if kind != "choice":
                raise SemanticBackendError(f"{backend}: 'tool' must be a choice, got {kind!r}")
            probs = answer.get("probabilities")
            if not isinstance(probs, dict) or not probs:
                raise SemanticBackendError(f"{backend}: 'tool' has no probabilities")
            total = 0.0
            for label, p in probs.items():
                if not isinstance(p, (int, float)) or p != p or p in (float("inf"), float("-inf")):
                    raise SemanticBackendError(f"{backend}: tool probability {label!r}={p!r} is not finite")
                total += float(p)
            if total <= 0:
                raise SemanticBackendError(f"{backend}: tool probabilities sum to {total}")
        else:
            if kind != "noul":
                raise SemanticBackendError(f"{backend}: {qid!r} must be 'noul', got {kind!r}")
            value = answer.get("noul")
            if not isinstance(value, (int, float)) or value != value:
                raise SemanticBackendError(f"{backend}: {qid!r} has non-finite value {value!r}")
    return resp


__all__ = [
    "REQUIRED_QUESTIONS",
    "SemanticBackendError",
    "SemanticDecisionBackend",
    "validate_response",
]
