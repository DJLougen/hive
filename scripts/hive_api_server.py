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
"""

from __future__ import annotations

import argparse
import os
from typing import Any

from hive import HiveStack
from hive.rule_fast import RuleFastHoneyComb

try:
    import uvicorn
    from fastapi import Depends, FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, Field

    _HAS_FASTAPI = True
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False


if _HAS_FASTAPI:
    from hive import __version__

    app = FastAPI(title="Hive Agent Memory", version=__version__)
    stack = HiveStack(honey_comb=RuleFastHoneyComb())

    # Optional bearer-token auth on the data endpoints. Set HIVE_API_TOKEN
    # to require ``Authorization: Bearer <token>`` on /route, /compress,
    # /remember, /recall. /health, /ready and /openapi.json stay public so
    # orchestrators and docs keep working. Unset = open (dev mode).
    _API_TOKEN = os.environ.get("HIVE_API_TOKEN")

    async def _require_auth(request: Request) -> None:
        if _API_TOKEN is None:
            return
        if request.headers.get("authorization") != f"Bearer {_API_TOKEN}":
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

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
        stack.remember(req.key, req.value, trust=req.trust)
        return {"status": "ok"}

    @app.get("/recall", dependencies=[Depends(_require_auth)])
    async def recall(key: str) -> dict:
        val = stack.recall(key)
        return {"key": key, "value": val}

    @app.get("/health")
    async def health() -> dict:
        return {"status": "alive"}

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


def main(argv: list[str] | None = None) -> int:
    if not _HAS_FASTAPI:
        print("ERROR: fastapi/uvicorn not installed. Run: pip install fastapi uvicorn")
        return 1
    p = argparse.ArgumentParser(description="Hive REST API server")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    args = p.parse_args(argv)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
