"""Optional bearer JWT authentication for Hive HTTP surfaces.

Set ``HIVE_REQUIRE_AUTH=true`` and configure ``HIVE_JWKS_URL`` or
``HIVE_JWT_PUBLIC_KEY`` before exposing the REST API or MCP SSE transport.
"""

from __future__ import annotations

import os
from typing import Any

from hive.auth import AuthError, JWTValidator


def require_auth_enabled() -> bool:
    """Return True when HTTP/MCP SSE endpoints must validate JWT bearer tokens."""
    return os.environ.get("HIVE_REQUIRE_AUTH", "").lower() in ("1", "true", "yes")


def verify_bearer_token(authorization: str | None) -> dict[str, Any]:
    """Validate ``Authorization: Bearer <jwt>`` when auth is required.

    Returns decoded claims when auth is enabled and the token is valid.
    Returns an empty dict when auth is disabled.
  """
    if not require_auth_enabled():
        return {}
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthError("Missing or invalid Authorization header (expected Bearer token)")
    token = authorization[7:].strip()
    return JWTValidator.from_env().validate(token)


__all__ = ["require_auth_enabled", "verify_bearer_token"]
