#!/usr/bin/env python3
"""REST/OpenAPI service wrapper for Hive.

Exposes Hive operations over HTTP with auto-generated OpenAPI docs.

Usage::

    python scripts/hive_api_server.py --port 8080

Endpoints:
    POST /route        → RouteDecision
    POST /compress     → CompressedTurn
    POST /remember     → MemoryNode
    GET  /recall       → value
    GET  /health       → 200 (liveness)
    GET  /ready        → 200/503 (readiness)
    GET  /openapi.json → OpenAPI schema

Environment:
    HIVE_*                → HiveConfig.from_env() (rate limit, tenant
                            isolation, TTL, max nodes, …). Invalid values
                            fail startup.
    HIVE_ROUTING_POLICY   → ``off`` (default: no routing policy loaded —
                            every /route escalates; nothing paid or unsafe
                            is loaded implicitly) or ``rule`` (local
                            RuleBasedRoutingPolicy). Any other value fails
                            startup.
    HIVE_API_TOKEN        → bearer token required on the data endpoints.
                            Unset = open localhost dev mode.
    HIVE_PRODUCTION       → ``1``/``true``/``yes``/``on`` requires a real
                            HIVE_API_TOKEN (non-empty, non-placeholder);
                            startup fails otherwise.
    HIVE_MEMORY_SNAPSHOT  → optional path to a RustBrain snapshot file.
                            Restored on startup when it exists (a corrupt
                            file fails startup — fail closed); every
                            successful /remember rewrites it atomically
                            (tempfile + os.replace in the same directory,
                            no fsync). Single-process scope: no
                            cross-replica sharing — do not run uvicorn
                            with --workers > 1, each worker would hold its
                            own divergent memory.
"""

from __future__ import annotations

import argparse
import logging
import os
import tempfile
from collections.abc import Mapping
from typing import Any

from hive import HiveStack, __version__
from hive.config import HiveConfig
from hive.rule_fast import RuleFastHoneyComb

_log = logging.getLogger("hive.api_server")

try:
    import uvicorn
    from fastapi import Depends, FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, Field

    _HAS_FASTAPI = True
except ModuleNotFoundError as exc:  # pragma: no cover
    # Only the genuinely optional server packages may degrade to
    # _HAS_FASTAPI=False. Anything else — a missing required dependency,
    # a broken submodule, a syntax or runtime error inside an installed
    # package — must propagate as a real failure, not a silent skip.
    if exc.name not in {"fastapi", "uvicorn"}:
        raise
    _HAS_FASTAPI = False


# Values that are never acceptable as a production bearer token: the
# placeholders shipped in deploy/k8s/secret.yaml and the obvious stand-ins.
_PLACEHOLDER_TOKENS = frozenset(
    {
        "replace-me",
        "changeme",
        "change-me",
        "todo",
        "placeholder",
        "your-token-here",
        "secret",
        "password",
        "test",
        "dev",
        "none",
        "null",
    }
)


def _env_flag(env: Mapping[str, str], name: str) -> bool:
    """Parse a boolean env var strictly.

    Unset/empty and explicit false forms → False; explicit true forms →
    True. A nonempty unrecognized value (e.g. ``HIVE_PRODUCTION=ture``)
    raises instead of silently reading as False — a typo must not open
    the API.
    """
    raw = env.get(name, "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("", "0", "false", "no", "off"):
        return False
    raise RuntimeError(
        f"invalid {name}={raw!r}: expected a boolean "
        "(1/0, true/false, yes/no, on/off)"
    )


def _resolve_api_token(env: Mapping[str, str]) -> str | None:
    """Return the configured bearer token, enforcing the production gate.

    ``HIVE_PRODUCTION`` is the explicit opt-in to a real deployment posture:
    when set, ``HIVE_API_TOKEN`` must be present, non-empty, and not a
    placeholder value. Anything else fails startup rather than silently
    serving an open or trivially-guessable API.
    """
    raw = env.get("HIVE_API_TOKEN")
    token = raw.strip() if raw else None
    if not _env_flag(env, "HIVE_PRODUCTION"):
        if not token:
            _log.warning(
                "HIVE_API_TOKEN unset — dev mode: data endpoints are "
                "unauthenticated. Bind to localhost or set a token."
            )
        return token or None
    if not token:
        raise RuntimeError(
            "HIVE_PRODUCTION is set but HIVE_API_TOKEN is missing or empty: "
            "refusing to start an unauthenticated production API"
        )
    if token.lower() in _PLACEHOLDER_TOKENS:
        raise RuntimeError(
            "HIVE_PRODUCTION is set but HIVE_API_TOKEN is a placeholder "
            f"({token!r}): set a real token, e.g. `openssl rand -hex 32`"
        )
    return token


def _resolve_routing_policy(env: Mapping[str, str]) -> Any | None:
    """Map HIVE_ROUTING_POLICY to a busybee policy object.

    ``off`` (default) loads nothing — no paid model, no unsigned checkpoint,
    no implicit fallback — and /route escalates every decision. ``rule``
    selects the local RuleBasedRoutingPolicy. Anything else is a config
    error and fails startup loudly.
    """
    name = env.get("HIVE_ROUTING_POLICY", "off").strip().lower()
    if name == "off":
        return None
    if name == "rule":
        from hive.harness import RuleBasedRoutingPolicy

        return RuleBasedRoutingPolicy()
    raise RuntimeError(
        f"invalid HIVE_ROUTING_POLICY={name!r}: expected 'off' or 'rule'"
    )


def _save_snapshot_atomic(brain: Any, path: str) -> None:
    """Persist a brain snapshot atomically: tempfile + os.replace, same dir.

    A crash mid-write leaves the previous snapshot intact instead of a
    truncated file that the next boot would fail closed on. Any failure
    propagates — persistence errors are never swallowed.
    """
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(
        prefix=".hive-snapshot-", suffix=".tmp", dir=directory
    )
    try:
        os.close(fd)
        brain.snapshot_to_file(tmp)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _restore_snapshot(brain: Any, path: str) -> None:
    """Restore a snapshot at startup; a corrupt file fails closed."""
    if not os.path.exists(path):
        # First boot: nothing to restore yet. The parent directory is
        # created so the first durable write does not fail on a missing dir.
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        _log.info("HIVE_MEMORY_SNAPSHOT=%s does not exist — starting empty", path)
        return
    restored = brain.restore_from_file(path)
    _log.info("Restored %d memory nodes from %s", restored, path)


def build_stack(env: Mapping[str, str] | None = None) -> HiveStack:
    """Construct the service's HiveStack from the environment.

    ``HiveConfig.from_env()`` reads the process environment directly
    (``env`` only covers the server-specific variables); invalid values
    raise before the stack is built. Snapshot restore errors propagate —
    a corrupt snapshot is a startup failure, not a warning.
    """
    env = os.environ if env is None else env
    config = HiveConfig.from_env()
    stack = HiveStack(
        busybee_policy=_resolve_routing_policy(env),
        honey_comb=RuleFastHoneyComb(),
        config=config,
    )
    snapshot_path = env.get("HIVE_MEMORY_SNAPSHOT")
    if snapshot_path:
        _restore_snapshot(stack.brain, snapshot_path)
    return stack


if _HAS_FASTAPI:
    # Request/response models live at module scope: with
    # ``from __future__ import annotations`` FastAPI resolves endpoint
    # annotations against module globals, so function-local models would
    # fail to resolve and every POST would 422.
    class RouteRequest(BaseModel):
        goal: str = Field(default="")
        available_tools: list[str] = Field(default_factory=list)
        step: int = Field(default=0, ge=0)

    class RouteResponse(BaseModel):
        tool: str
        args: dict[str, Any]
        confidence: float
        escalated: bool
        source: str

    class CompressRequest(BaseModel):
        role: str
        content: str

    class CompressResponse(BaseModel):
        role: str
        content: str
        label: str

    class RememberRequest(BaseModel):
        key: str
        value: Any
        trust: float = Field(default=1.0, ge=0.0, le=1.0)


def create_app(
    stack: HiveStack,
    *,
    api_token: str | None = None,
    snapshot_path: str | None = None,
) -> FastAPI:
    """Build the FastAPI app around an existing stack.

    ``api_token`` gates the data endpoints (None = open dev mode).
    ``snapshot_path`` enables durable writes: every successful /remember
    persists the brain atomically, and a persistence failure returns a
    non-success status instead of silently acknowledging the write.
    """
    app = FastAPI(title="Hive Agent Memory", version=__version__)

    async def _require_auth(request: Request) -> None:
        if api_token is None:
            return
        if request.headers.get("authorization") != f"Bearer {api_token}":
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")


    @app.post("/route", response_model=RouteResponse, dependencies=[Depends(_require_auth)])
    async def route(req: RouteRequest) -> RouteResponse:
        d = stack.route(req.model_dump())
        return RouteResponse(
            tool=d.tool,
            args=d.args,
            confidence=d.confidence,
            escalated=d.escalated,
            source=d.source,
        )

    @app.post("/compress", response_model=CompressResponse, dependencies=[Depends(_require_auth)])
    async def compress(req: CompressRequest) -> CompressResponse:
        c = stack.compress(req.role, req.content)
        return CompressResponse(role=c.role, content=c.content, label=c.label)

    @app.post("/remember", dependencies=[Depends(_require_auth)])
    async def remember(req: RememberRequest) -> dict:
        try:
            node = stack.remember(req.key, req.value, trust=req.trust)
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"memory write rejected: {exc}"
            ) from exc
        if node is None:
            raise HTTPException(status_code=500, detail="memory write rejected")
        if snapshot_path:
            try:
                _save_snapshot_atomic(stack.brain, snapshot_path)
            except Exception as exc:
                raise HTTPException(
                    status_code=500, detail=f"snapshot persistence failed: {exc}"
                ) from exc
        return {"status": "ok"}

    @app.get("/recall", dependencies=[Depends(_require_auth)])
    async def recall(key: str) -> dict:
        val = stack.recall(key)
        return {"key": key, "value": val}

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "alive",
            "mode": "production" if api_token else "dev",
        }

    @app.get("/ready")
    async def ready() -> JSONResponse:
        try:
            from hive.health import is_healthy

            ready, _backends = is_healthy(stack)
            if ready:
                return JSONResponse({"status": "ready"})
        except Exception:
            pass
        return JSONResponse({"status": "not_ready"}, status_code=503)

    return app


def _app_from_env(env: Mapping[str, str] | None = None) -> FastAPI:
    """Resolve server env vars, build the stack, and wire the app."""
    env = os.environ if env is None else env
    api_token = _resolve_api_token(env)
    stack = build_stack(env)
    return create_app(stack, api_token=api_token,
                      snapshot_path=env.get("HIVE_MEMORY_SNAPSHOT"))


if _HAS_FASTAPI:
    # Module-level app/stack for `uvicorn scripts.hive_api_server:app` and
    # tests; main() builds a fresh app so CLI runs always see current env.
    stack = build_stack()
    app = create_app(
        stack,
        api_token=_resolve_api_token(os.environ),
        snapshot_path=os.environ.get("HIVE_MEMORY_SNAPSHOT"),
    )


def main(argv: list[str] | None = None) -> int:
    if not _HAS_FASTAPI:
        print("ERROR: fastapi/uvicorn not installed. Run: pip install fastapi uvicorn")
        return 1
    p = argparse.ArgumentParser(description="Hive REST API server")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    args = p.parse_args(argv)
    try:
        app_instance = _app_from_env()
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    if os.environ.get("HIVE_API_TOKEN") is None:
        print(
            "WARNING: HIVE_API_TOKEN unset — dev mode, data endpoints are "
            "unauthenticated. Set HIVE_PRODUCTION=1 to enforce a token."
        )
    uvicorn.run(app_instance, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
