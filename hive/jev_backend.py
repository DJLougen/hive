"""Jev backend — TypeSafe Jev as a :class:`SemanticDecisionBackend`.

This adapter isolates every Jev-specific detail: the HTTP call, the request
shape, and the response normalization. Hive routing code never sees any of it.

Construction takes an injected ``client`` so tests can mock without network, and
so a different transport (or a local Jev-compatible server) can be substituted
without touching this module's callers.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from hive.semantic_backend import SemanticBackendError, validate_response

#: Default endpoint for the hosted API.
DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"


class _UrllibClient:
    """Minimal default transport: POST JSON, return the decoded body."""

    def __init__(self, *, endpoint: str, api_key: str, timeout_s: float) -> None:
        self.endpoint = endpoint
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.last_usage: dict[str, Any] | None = None

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as fh:
                body = json.loads(fh.read().decode())
        except urllib.error.HTTPError as exc:  # pragma: no cover - transport
            raise SemanticBackendError(f"jev: HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:  # pragma: no cover
            raise SemanticBackendError(f"jev: transport error: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise SemanticBackendError(f"jev: response was not JSON: {exc}") from exc
        if isinstance(body, dict) and isinstance(body.get("usage"), dict):
            self.last_usage = body["usage"]
        return body


class JevBackend:
    """Normalize TypeSafe Jev responses into the Hive semantic contract."""

    backend_name = "jev"

    def __init__(
        self,
        *,
        client: Any | None = None,
        model: str = DEFAULT_MODEL,
        endpoint: str = DEFAULT_ENDPOINT,
        api_key: str | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        if client is None:
            if not api_key:
                raise SemanticBackendError(
                    "JevBackend needs an api_key (or an injected client)"
                )
            client = _UrllibClient(endpoint=endpoint, api_key=api_key, timeout_s=timeout_s)
        self._client = client
        self.model = model
        self.model_revision: str | None = None
        self.last_usage: dict[str, Any] | None = None

    def decide(
        self,
        *,
        state: dict[str, Any],
        questions: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Ask Jev, then normalize. Raises SemanticBackendError on any failure."""
        payload = {"state": state, "model": self.model, "questions": questions}
        t0 = time.perf_counter()
        try:
            body = self._client.post(payload)
        except SemanticBackendError:
            raise
        except Exception as exc:
            raise SemanticBackendError(f"jev: client raised {type(exc).__name__}: {exc}") from exc
        latency_ms = (time.perf_counter() - t0) * 1000.0

        if not isinstance(body, dict):
            raise SemanticBackendError("jev: response was not a JSON object")
        answers = body.get("answers")
        revision = body.get("model")
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else None

        normalized = {
            "answers": answers,
            "backend": self.backend_name,
            "model_revision": revision,
            "latency_ms": round(latency_ms, 3),
            "request_id": body.get("id"),
            "usage": usage,
            "raw": body,
        }
        # Shape-check before returning: a malformed body must fail, not route.
        validate_response(normalized, backend=self.backend_name)
        self.model_revision = revision
        if usage is not None:
            self.last_usage = usage
        return normalized


__all__ = ["DEFAULT_ENDPOINT", "DEFAULT_MODEL", "JevBackend"]
