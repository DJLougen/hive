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
from typing import Any

from hive import HiveStack
from hive.config import HiveConfig
from hive.rule_fast import RuleFastHoneyComb


try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, Field
    import uvicorn

    _HAS_FASTAPI = True
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False


if _HAS_FASTAPI:
    app = FastAPI(title="Hive Agent Memory", version="0.5.0")
    stack = HiveStack(honey_comb=RuleFastHoneyComb(), config=HiveConfig.from_env())

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

    @app.post("/route", response_model=RouteResponse)
    async def route(req: RouteRequest) -> RouteResponse:
        d = stack.route(req.model_dump())
        return RouteResponse(
            tool=d.tool,
            args=d.args,
            confidence=d.confidence,
            escalated=d.escalated,
            source=d.source,
        )

    @app.post("/compress", response_model=CompressResponse)
    async def compress(req: CompressRequest) -> CompressResponse:
        c = stack.compress(req.role, req.content)
        return CompressResponse(role=c.role, content=c.content, label=c.label)

    @app.post("/remember")
    async def remember(req: RememberRequest) -> dict:
        stack.remember(req.key, req.value, trust=req.trust)
        return {"status": "ok"}

    @app.get("/recall")
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
