"""OpenAI-compatible HTTP server for CPU action policy inference.

Provides endpoints for chat completions, model listing, and health checks.
Supports multi-model serving, session tracking, workflow awareness, and
online learning via the /v1/learn endpoint.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from pathlib import Path

from busybee_cpu.policy import CpuActionPolicy
from busybee_cpu.rows import tool_names as _tool_names
from busybee_cpu.tracing import create_tracer
from busybee_cpu.workflow import WorkflowTracker

log = logging.getLogger("busybee_cpu.server")

MAX_BODY = 2 * 1024 * 1024  # 2 MiB request body limit
CORS_ORIGINS = "*"


# ---------------------------------------------------------------------------
# session tracking
# ---------------------------------------------------------------------------


class SessionTracker:
    """Tracks per-session prediction history for workflow awareness."""

    __slots__ = ("_sessions", "_max_history")

    def __init__(self, max_history: int = 20) -> None:
        self._sessions: dict[str, list[dict[str, Any]]] = {}
        self._max_history = max_history

    def record(self, session_id: str, action: dict[str, Any]) -> None:
        if session_id not in self._sessions:
            self._sessions[session_id] = []
        self._sessions[session_id].append({
            "tool": action.get("tool"),
            "confidence": action.get("confidence"),
            "ts": time.time(),
        })
        if len(self._sessions[session_id]) > self._max_history:
            self._sessions[session_id] = self._sessions[session_id][-self._max_history :]

    def get_history(self, session_id: str) -> list[str]:
        return [str(e.get("tool") or "") for e in self._sessions.get(session_id, [])]

    def inject_context(self, row: dict[str, Any], session_id: str) -> None:
        history = self.get_history(session_id)
        if history:
            if not isinstance(row.get("state"), dict):
                row["state"] = {"recent_observations": []}
            row["state"]["session_actions"] = history
            row["state"]["last_tool"] = history[-1]

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    def cleanup(self, max_age: float = 3600.0) -> int:
        now = time.time()
        expired = [
            sid
            for sid, entries in self._sessions.items()
            if entries and now - entries[-1]["ts"] > max_age
        ]
        for sid in expired:
            del self._sessions[sid]
        return len(expired)


# ---------------------------------------------------------------------------
# server
# ---------------------------------------------------------------------------


def strict_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def extract_json_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def parse_messages(messages: list[dict[str, Any]]) -> dict[str, Any]:
    combined = "\n".join(str(message.get("content") or "") for message in messages)
    parsed = extract_json_object(combined)
    if parsed and ("state" in parsed or "goal" in parsed):
        return parsed
    return {"goal": combined, "state": {"recent_observations": [combined]}, "available_tools": []}


class PolicyServer(ThreadingHTTPServer):
    policies: dict[str, CpuActionPolicy]
    default_model: str
    sessions: SessionTracker
    workflow: WorkflowTracker
    tracer: Any
    confidence_threshold: float


class Handler(BaseHTTPRequestHandler):
    server: PolicyServer

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info(fmt, *args)

    # --- helpers ---

    def _set_cors(self) -> None:
        self.send_header("access-control-allow-origin", CORS_ORIGINS)
        self.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
        self.send_header("access-control-allow-headers", "content-type, x-model, x-session-id")

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self._set_cors()
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes | None:
        length = int(self.headers.get("content-length") or 0)
        if length > MAX_BODY:
            self.send_json(413, {"error": f"Request body exceeds {MAX_BODY} byte limit"})
            return None
        return self.rfile.read(length) if length > 0 else b"{}"

    def _get_model(self) -> CpuActionPolicy:
        model_name = self.headers.get("x-model") or self.server.default_model
        policy = self.server.policies.get(model_name)
        if policy is None:
            policy = self.server.policies.get(self.server.default_model)
        if policy is None:
            raise KeyError(f"No model loaded: {model_name}")
        return policy

    def _session_id(self) -> str:
        return self.headers.get("x-session-id") or "default"

    # --- HTTP verbs ---

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._set_cors()
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_json(200, {
                "ok": True,
                "models": list(self.server.policies.keys()),
                "sessions": self.server.sessions.session_count,
            })
            return
        if self.path == "/v1/models":
            data = [
                {"id": name, "object": "model", "created": 0, "owned_by": "busybee-cpu"}
                for name in self.server.policies
            ]
            self.send_json(200, {"object": "list", "data": data})
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path == "/v1/chat/completions":
            self._handle_chat()
        elif self.path == "/v1/learn":
            self._handle_learn()
        else:
            self.send_json(404, {"error": "not found"})

    # --- endpoint handlers ---

    def _handle_chat(self) -> None:
        t0 = time.monotonic()
        body = self._read_body()
        if body is None:
            return
        try:
            request = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json(400, {"error": "invalid JSON body"})
            return

        policy = self._get_model()
        row = parse_messages(request.get("messages") or [])

        session_id = self._session_id()
        self.server.sessions.inject_context(row, session_id)

        # inject session history into state for workflow awareness
        state = row.get("state") if isinstance(row.get("state"), dict) else {}
        history = self.server.workflow.get_history(session_id)
        if history:
            state["last_tool"] = history[-1]

        action = policy.predict(
            row,
            confidence_threshold=self.server.confidence_threshold,
        )

        # workflow suggestion
        avail = set(_tool_names(row))
        suggestion = self.server.workflow.suggest(
            str(action.get("tool") or ""),
            session_id=session_id,
            available=avail,
        )
        if suggestion:
            action["workflow_suggestion"] = suggestion

        # record in workflow tracker
        self.server.workflow.record(session_id, str(action.get("tool") or ""))
        self.server.sessions.record(session_id, action)

        # strip internal fields
        action.pop("arg_template", None)
        content = strict_json(action)

        latency = round((time.monotonic() - t0) * 1000, 1)
        log.info(
            "predict session=%s tool=%s confidence=%s latency=%.1fms",
            session_id,
            action.get("tool"),
            action.get("confidence"),
            latency,
        )

        self.send_json(200, {
            "id": "chatcmpl-busybee-cpu",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": self.server.default_model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })

    def _handle_learn(self) -> None:
        body = self._read_body()
        if body is None:
            return
        try:
            request = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json(400, {"error": "invalid JSON body"})
            return

        row = request.get("row") or request
        if not isinstance(row, dict) or not (row.get("target_action") or {}).get("tool"):
            self.send_json(400, {"error": "row must contain target_action.tool"})
            return

        policy = self._get_model()
        policy.add_correction(row)
        self.send_json(200, {"ok": True, "corrections": len(policy.corrections)})


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve a CPU action policy through an OpenAI-compatible endpoint.")
    parser.add_argument("--model", required=True, action="append", help="Path to a trained joblib policy. May be repeated for multi-model serving.")
    parser.add_argument("--model-name", action="append", default=[], help="Name for the corresponding --model. Defaults to filename stem.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--exposed-model", default=None, help="Default model name exposed via /v1/models.")
    parser.add_argument("--confidence-threshold", type=float, default=0.0, help="Escalate when confidence is below this value.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    policies: dict[str, CpuActionPolicy] = {}
    for i, model_path in enumerate(args.model):
        name = args.model_name[i] if i < len(args.model_name) else Path(model_path).stem
        policy = CpuActionPolicy.load(model_path)
        policy.confidence_threshold = args.confidence_threshold
        policy.tracer = create_tracer(enabled=True)
        policies[name] = policy
        log.info("Loaded model %r from %s", name, model_path)

    default_name = args.exposed_model or next(iter(policies))

    # If --exposed-model is provided and doesn't match any key,
    # rename the first policy to match so lookups by exposed name work.
    if args.exposed_model and args.exposed_model not in policies and len(policies) == 1:
        old_key = next(iter(policies))
        policies[args.exposed_model] = policies.pop(old_key)
        log.info("Renamed model key %r -> %r for exposed name", old_key, args.exposed_model)

    server = PolicyServer((args.host, args.port), Handler)
    server.policies = policies
    server.default_model = default_name
    server.sessions = SessionTracker()
    server.workflow = WorkflowTracker()
    server.tracer = create_tracer(enabled=True)
    server.confidence_threshold = args.confidence_threshold

    print(f"BusyBee CPU server listening on http://{args.host}:{args.port}", flush=True)
    print(f"Models: {list(policies.keys())} (default: {default_name})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
