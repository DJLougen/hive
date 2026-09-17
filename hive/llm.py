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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from hive.circuitbreaker import CircuitBreaker

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
    tool_calls: list[dict[str, Any]] | None = None


# ---------------------------------------------------------------------------
# Endpoint probing
# ---------------------------------------------------------------------------


def probe_endpoint(endpoint: str, *, timeout: float = 2.0) -> dict[str, Any]:
    """Confirm a model server is reachable.

    Returns the parsed ``/v1/models`` JSON on success. Raises
    :class:`RuntimeError` on any failure with a one-line diagnostic.
    """
    url = f"{endpoint.rstrip('/')}/v1/models"
    _validate_url(url)
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
        self, endpoint: str, model_name: str, *, timeout: float = 60.0,
        circuit_breaker: CircuitBreaker | None = None,
        api_key: str | None = None,
    ) -> None:
        _validate_url(endpoint)
        self.endpoint = endpoint.rstrip("/")
        self.model_name = model_name
        self.timeout = timeout
        self.circuit_breaker = circuit_breaker
        self.api_key = api_key

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "hive-agent-memory/0.6",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def chat(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        max_tokens: int = 256,
        temperature: float = 0.0,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
    ) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": [dict(m) for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url=f"{self.endpoint}/v1/chat/completions",
            data=data,
            method="POST",
            headers=self._headers(),
        )
        t0 = time.perf_counter()
        try:
            if self.circuit_breaker is not None:
                with self.circuit_breaker.call(), urllib.request.urlopen(  # nosec B310 — validated by _validate_url
                    req, timeout=self.timeout
                ) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
            else:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310 — validated by _validate_url
                    body = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"chat failed against {self.endpoint}: {exc}") from exc
        elapsed = time.perf_counter() - t0
        choice = body["choices"][0]
        usage = body.get("usage") or {}
        return ModelResponse(
            text=choice["message"].get("content") or "",
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            duration_s=elapsed,
            model=body.get("model", self.model_name),
            finish_reason=choice.get("finish_reason", ""),
            tool_calls=choice["message"].get("tool_calls"),
        )

    async def achat(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        max_tokens: int = 256,
        temperature: float = 0.0,
    ) -> ModelResponse:
        """Async chat via httpx (preferred for FastAPI / high-throughput)."""
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError(
                "httpx is required for async LLM calls; pip install 'hive-agent-memory[http]'"
            ) from exc

        payload = {
            "model": self.model_name,
            "messages": [dict(m) for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        t0 = time.perf_counter()
        try:
            if self.circuit_breaker is not None:
                self.circuit_breaker._before_call()
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.endpoint}/v1/chat/completions",
                    json=payload,
                    headers=self._headers(),
                )
                resp.raise_for_status()
                body = resp.json()
            if self.circuit_breaker is not None:
                self.circuit_breaker._on_success()
        except Exception as exc:
            if self.circuit_breaker is not None:
                self.circuit_breaker._on_failure()
            raise RuntimeError(f"async chat failed against {self.endpoint}: {exc}") from exc
        elapsed = time.perf_counter() - t0
        choice = body["choices"][0]
        usage = body.get("usage") or {}
        return ModelResponse(
            text=choice["message"].get("content") or "",
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            duration_s=elapsed,
            model=body.get("model", self.model_name),
            finish_reason=choice.get("finish_reason", ""),
            tool_calls=choice["message"].get("tool_calls"),
        )


class EchoBackend:
    """Deterministic stub. Returns a small echo of the last user message."""

    def chat(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        max_tokens: int = 256,
        temperature: float = 0.0,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
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
    circuit_breaker: CircuitBreaker | None = None,
    api_key: str | None = None,
) -> Any:
    """Return a backend by name.

    ``name`` is one of ``"vllm"``, ``"llama.cpp"``, ``"openai"`` (any
    OpenAI-compatible endpoint, e.g. Groq / OpenAI / Together) or ``"echo"``.
    For the HTTP backends, ``endpoint`` is required; ``api_key`` is sent as a
    bearer token when provided.
    """
    if name == "echo":
        return EchoBackend()
    if name in ("vllm", "llama.cpp", "openai"):
        if not endpoint:
            raise ValueError(f"backend {name!r} requires --inference-endpoint")
        return _OpenAICompatBackend(
            endpoint=endpoint, model_name=model, circuit_breaker=circuit_breaker,
            api_key=api_key,
        )
    raise ValueError(f"unknown backend {name!r}; choose vllm, llama.cpp, openai, or echo")


async def achat(
    backend: Any,
    messages: Sequence[Mapping[str, str]],
    **kwargs: Any,
) -> ModelResponse:
    """Dispatch async chat to a backend that supports ``achat``."""
    if hasattr(backend, "achat"):
        return await backend.achat(messages, **kwargs)
    if hasattr(backend, "chat"):
        return backend.chat(messages, **kwargs)
    raise TypeError(f"backend {type(backend)!r} has no chat/achat method")
