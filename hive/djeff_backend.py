"""d-Jeff backend — the future local semantic router.

This is deliberately a placeholder with the *identical* interface to
:class:`hive.jev_backend.JevBackend`. When a d-Jeff checkpoint exists, only this
module needs a substantive implementation; the semantic policy, the cascade, the
argument resolvers, telemetry, feedback records, and the benchmark harness are
all already backend-agnostic.

It raises :class:`~hive.semantic_backend.SemanticBackendError` on use, which the
semantic policy turns into LLM escalation — so enabling ``semantic_primary=djeff``
before a model is configured fails safe rather than routing on nothing.
"""

from __future__ import annotations

from typing import Any

from hive.semantic_backend import SemanticBackendError, validate_response


class DJeffBackend:
    """Placeholder local semantic backend. Same contract as JevBackend."""

    backend_name = "djeff"
    model_revision: str | None = None

    def __init__(self, *, model: str | None = None, client: Any | None = None) -> None:
        self.model = model
        self._client = client

    def decide(
        self,
        *,
        state: dict[str, Any],
        questions: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        if self._client is None:
            raise SemanticBackendError(
                "d-Jeff checkpoint not configured: set HIVE_DJEFF_MODEL / "
                "semantic djeff_model, or inject a client"
            )
        # A configured client is expected to return the normalized shape already
        # (the same contract the Jev adapter produces). Validate before trusting.
        resp = self._client.post({"state": state, "questions": questions})
        if not isinstance(resp, dict):
            raise SemanticBackendError("djeff: client returned a non-dict response")
        if not resp.get("backend"):
            resp["backend"] = self.backend_name
        if resp.get("model_revision") is None:
            resp["model_revision"] = self.model
        if resp.get("latency_ms") is None:
            resp["latency_ms"] = 0.0
        validate_response(resp, backend=self.backend_name)
        return resp


__all__ = ["DJeffBackend"]
