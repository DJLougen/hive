"""hive/llm.py — unified LLM client used by the benchmark and the integration
example.

The client knows three backends, all OpenAI-compatible on the wire:

* **vllm** — the vLLM HTTP server (``vllm serve ... --port 8000``).
* **llama.cpp** — the ``llama-server`` binary (``llama-server -m model.gguf
  --port 8080``). Same wire format as vLLM.
* **echo** — a deterministic stub for CI / laptops / smoke tests.

A small probe (``probe_endpoint``) hits ``/v1/models`` to confirm the
server is up *before* we time the chat call. Without this, the benchmark
would attribute connection timeouts to the model and the user would see
nonsensical token/s numbers.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

_log = logging.getLogger("hive.llm")


def _validate_url(url: str) -> None:
    """Ensure URL uses http or https scheme (bandit B310)."""
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"URL must use http or https scheme, got: {url!r}")


@dataclass(slots=True)
class ModelResponse:
    text: str
    prompt_tokens: int
    completion_tokens: int
    duration_s: float
    model: str = ""
    finish_reason: str = ""


# ---------------------------------------------------------------------------
# Endpoint probing
# ---------------------------------------------------------------------------


def probe_endpoint(endpoint: str, *, timeout: float = 2.0) -> dict[str, Any]:
    """Confirm a model server is reachable.

    Returns the parsed ``/v1/models`` JSON on success. Raises
    :class:`RuntimeError` on any failure with a one-line diagnostic.
    """
    url = f"{endpoint.rstrip('/')}/v1/models"
    req = urllib.request.Request(url=url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 — validated by _validate_url
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(
            f"model server at {endpoint!r} is unreachable ({exc}). "
            f"Start it (e.g. `vllm serve <model> --port 8000`) or pass --backend echo."
        ) from exc


def discover_local_endpoints() -> list[tuple[str, str]]:
    """Return a list of (backend, url) pairs that look live.

    Used by ``hive_benchmark.py`` to auto-pick a model server when the
    user does not pass ``--inference-endpoint``. We try vLLM (8000) and
    llama.cpp (8080) in that order.
    """
    found: list[tuple[str, str]] = []
    for backend, url in (
        ("vllm", "http://127.0.0.1:8000"),
        ("llama.cpp", "http://127.0.0.1:8080"),
    ):
        try:
            probe_endpoint(url, timeout=0.5)
            found.append((backend, url))
        except RuntimeError:
            continue
    return found


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class _OpenAICompatBackend:
    """Backend for any server speaking the OpenAI /v1/chat/completions API."""

    def __init__(
        self, endpoint: str, model_name: str, *, timeout: float = 60.0
    ) -> None:
        _validate_url(endpoint)
        self.endpoint = endpoint.rstrip("/")
        self.model_name = model_name
        self.timeout = timeout

    def chat(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        max_tokens: int = 256,
        temperature: float = 0.0,
    ) -> ModelResponse:
        payload = {
            "model": self.model_name,
            "messages": [dict(m) for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url=f"{self.endpoint}/v1/chat/completions",
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310 — validated by _validate_url
                body = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"chat failed against {self.endpoint}: {exc}") from exc
        elapsed = time.perf_counter() - t0
        choice = body["choices"][0]
        usage = body.get("usage") or {}
        return ModelResponse(
            text=choice["message"]["content"],
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            duration_s=elapsed,
            model=body.get("model", self.model_name),
            finish_reason=choice.get("finish_reason", ""),
        )


class EchoBackend:
    """Deterministic stub. Returns a small echo of the last user message."""

    def chat(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        max_tokens: int = 256,
        temperature: float = 0.0,
    ) -> ModelResponse:
        last = messages[-1]["content"] if messages else ""
        return ModelResponse(
            text="[echo] " + last[:200],
            prompt_tokens=sum(len(m["content"]) // 4 for m in messages),
            completion_tokens=50,
            duration_s=0.0,
            model="echo",
            finish_reason="stop",
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_backend(
    name: str,
    *,
    endpoint: str | None = None,
    model: str = "hive-default",
) -> Any:
    """Return a backend by name.

    ``name`` is one of ``"vllm"``, ``"llama.cpp"`` or ``"echo"``. For the
    HTTP backends, ``endpoint`` is required.
    """
    if name == "echo":
        return EchoBackend()
    if name in ("vllm", "llama.cpp"):
        if not endpoint:
            raise ValueError(f"backend {name!r} requires --inference-endpoint")
        return _OpenAICompatBackend(endpoint=endpoint, model_name=model)
    raise ValueError(f"unknown backend {name!r}; choose vllm, llama.cpp, or echo")
